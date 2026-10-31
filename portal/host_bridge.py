from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable, Mapping, Protocol, TYPE_CHECKING

from .adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from .route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)

if TYPE_CHECKING:
    from .session import PortalSessionResult
    from .wave_runtime import PortalWavePacket


_HOST_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_host_routes (
    adapter_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    effect_capabilities_json TEXT NOT NULL,
    authorized_effects_json TEXT NOT NULL,
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    attached INTEGER NOT NULL CHECK (attached IN (0, 1)),
    current INTEGER NOT NULL CHECK (current IN (0, 1)),
    preference INTEGER NOT NULL,
    observed_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (
        adapter_id, route_id, node_id, target_kind, target_id
    )
);

CREATE TABLE IF NOT EXISTS portal_host_node_occupancy (
    node_id TEXT PRIMARY KEY,
    occupied_slots INTEGER NOT NULL CHECK (occupied_slots >= 0),
    observed_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_host_dispatches (
    dispatch_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    wave_run_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    ref TEXT NOT NULL,
    exact_head TEXT NOT NULL,
    node_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('QUEUED', 'ATTEMPTED')),
    attempt_id TEXT,
    dispatch_evidence_id TEXT,
    attempted_at REAL,
    reconciliation_state TEXT,
    reconciliation_evidence_id TEXT,
    reconciled_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id, wave_run_id, subject_kind, subject_id)
);
"""

_ALLOWED_RECONCILIATION_STATES = frozenset(
    {
        "IN_PROGRESS",
        "OUTCOME_UNKNOWN",
        "VERIFIED_COMPLETE",
        "VERIFIED_HELD",
    }
)
_FINAL_RECONCILIATION_STATES = frozenset(
    {"VERIFIED_COMPLETE", "VERIFIED_HELD"}
)
_HOST_MONOTONIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_host_monotonic_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    state_root TEXT NOT NULL,
    scope TEXT NOT NULL
);
"""


class HostDispatchMonotonicAnchor(Protocol):
    """Separately authenticated, nonrollbackable host-owned state CAS.

    The in-process protocol cannot itself establish that the implementation
    actually meets those properties. Tests may inject a cloneable fake.
    """

    def current(self, scope: str) -> tuple[int, str] | None: ...

    def compare_advance(self, scope: str, epoch: int,
                        prior_root: str, next_root: str) -> bool: ...


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PortalHostBridgeStore:
    """Durable browser/host boundary for plugin or workstation execution.

    The bridge never performs an external effect. It stores expiring route
    advertisements, exact dispatch envelopes, pre-effect attempt markers and
    host-supplied reconciliation evidence so a chat/browser/runtime loss cannot
    silently turn an ambiguous effect into a retry on another route.
    """

    def __init__(self, path: Path, *,
                 monotonic_anchor: HostDispatchMonotonicAnchor | None = None,
                 monotonic_scope: str | None = None) -> None:
        if (monotonic_anchor is None) != (monotonic_scope is None):
            raise ValueError("monotonic anchor and scope must be configured together")
        if monotonic_scope is not None and (
            not isinstance(monotonic_scope, str)
            or not 1 <= len(monotonic_scope) <= 128
            or any(not (char.isascii() and (char.isalnum() or char in "_.:-"))
                   for char in monotonic_scope)
        ):
            raise ValueError("invalid host monotonic scope")
        if monotonic_anchor is not None and (
            not callable(getattr(monotonic_anchor, "current", None))
            or not callable(getattr(monotonic_anchor, "compare_advance", None))
        ):
            raise TypeError("host monotonic CAS protocol required")
        self._monotonic_anchor = monotonic_anchor
        self._monotonic_scope = monotonic_scope
        self._pending_anchor_state: tuple[int, str] | None = None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        # Once a database has been enrolled in host-monotonic custody, a
        # legacy unanchored reader must not silently bypass that checkpoint.
        # A *pre-enrollment* database snapshot still requires an external
        # effect gate: SQLite alone cannot detect its own restoration.
        if self._monotonic_anchor is None and self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='portal_host_monotonic_checkpoint'"
        ).fetchone() is not None:
            self.connection.close()
            raise PermissionError("host-monotonic database requires an anchor")
        self.connection.executescript(_HOST_SCHEMA)
        if self._monotonic_anchor is not None:
            self.connection.executescript(_HOST_MONOTONIC_SCHEMA)

    def _state_root(self) -> str:
        """Canonical commitment to ALL route, occupancy and dispatch rows.

        Root covers complete persisted authority membership, not merely the
        monotonic counter. Deletion, insertion, or mutation invalidates it.
        """
        tables = (
            ("portal_host_routes", "adapter_id,route_id,node_id,target_kind,target_id"),
            ("portal_host_node_occupancy", "node_id"),
            ("portal_host_dispatches", "dispatch_id"),
        )
        state = {
            table: [dict(row) for row in self.connection.execute(
                f"SELECT * FROM {table} ORDER BY {order}"
            )]
            for table, order in tables
        }
        return hashlib.sha256(_canonical_json({
            "schema": "PORTAL_HOST_STATE_V1", "tables": state,
        }).encode("utf-8")).hexdigest()

    def _external_current(self) -> tuple[int, str] | None:
        assert self._monotonic_anchor is not None
        assert self._monotonic_scope is not None
        try:
            return self._monotonic_anchor.current(self._monotonic_scope)
        except Exception:
            raise PermissionError("host monotonic authority unavailable") from None

    def provision_host_checkpoint(self) -> None:
        """Host provisioning only; runtime never silently bootstraps authority.

        The host MUST independently provision exactly (0, initial state root)
        and own the scope/key. A caller-controllable fake cannot prove this.
        """
        if self._monotonic_anchor is None:
            raise PermissionError("host monotonic authority is not configured")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT 1 FROM portal_host_monotonic_checkpoint WHERE id=1"
            ).fetchone()
            root = self._state_root()
            if row is not None or self._external_current() != (0, root):
                raise PermissionError("host checkpoint cannot be provisioned")
            self.connection.execute(
                "INSERT INTO portal_host_monotonic_checkpoint (id,epoch,state_root,scope) "
                "VALUES (1,0,?,?)", (root, self._monotonic_scope)
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _check_host_checkpoint(self) -> tuple[int, str] | None:
        if self._monotonic_anchor is None:
            return None
        row = self.connection.execute(
            "SELECT epoch,state_root,scope FROM portal_host_monotonic_checkpoint WHERE id=1"
        ).fetchone()
        if (row is None or type(row[0]) is not int or row[0] < 0
            or not isinstance(row[1], str) or len(row[1]) != 64
            or row[2] != self._monotonic_scope
            or self._state_root() != row[1]
            or self._external_current() != (row[0], row[1])):
            raise PermissionError("host monotonic checkpoint mismatch")
        return int(row[0]), str(row[1])

    def _begin_checked(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._pending_anchor_state = self._check_host_checkpoint()
        except BaseException:
            self.connection.rollback()
            self._pending_anchor_state = None
            raise

    def _commit_checked(self) -> None:
        prior = self._pending_anchor_state
        if prior is not None:
            epoch, old_root = prior
            row = self.connection.execute(
                "SELECT epoch,state_root,scope FROM portal_host_monotonic_checkpoint WHERE id=1"
            ).fetchone()
            if (row is None or (row[0], row[1]) != prior
                or row[2] != self._monotonic_scope
                or self._external_current() != prior):
                raise PermissionError("host monotonic checkpoint changed")
            new_root = self._state_root()
            if new_root != old_root:
                assert self._monotonic_anchor is not None
                assert self._monotonic_scope is not None
                try:
                    advanced = self._monotonic_anchor.compare_advance(
                        self._monotonic_scope, epoch, old_root, new_root)
                except Exception:
                    raise PermissionError("host monotonic CAS unavailable") from None
                if advanced is not True:
                    raise PermissionError("host monotonic CAS rejected")
                self.connection.execute(
                    "UPDATE portal_host_monotonic_checkpoint SET epoch=?,state_root=? "
                    "WHERE id=1", (epoch + 1, new_root)
                )
        self.connection.commit()
        self._pending_anchor_state = None

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _route_storage_key(
        route: PortalRouteAdvertisement,
    ) -> tuple[str, str, str, str, str]:
        return (
            route.adapter_id,
            route.route_id,
            route.node_id,
            route.target_kind,
            route.target_id,
        )

    @staticmethod
    def _stored_route(
        route: PortalRouteAdvertisement,
        row: sqlite3.Row,
    ) -> PortalRouteAdvertisement:
        return PortalRouteAdvertisement(
            adapter_id=route.adapter_id,
            route_id=route.route_id,
            node_id=route.node_id,
            target_kind=route.target_kind,
            target_id=route.target_id,
            capabilities=tuple(json.loads(str(row["capabilities_json"]))),
            effect_capabilities=tuple(
                json.loads(str(row["effect_capabilities_json"]))
            ),
            authorized_effects=tuple(
                json.loads(str(row["authorized_effects_json"]))
            ),
            available=bool(row["available"]),
            attached=bool(row["attached"]),
            current=bool(row["current"]),
            preference=int(row["preference"]),
        )

    def advertise_routes(
        self,
        routes: Iterable[PortalRouteAdvertisement],
        *,
        ttl_seconds: float,
        observed_at: float | None = None,
    ) -> tuple[PortalRouteAdvertisement, ...]:
        if ttl_seconds <= 0:
            raise ValueError("route advertisement ttl_seconds must be positive")
        route_tuple = tuple(routes)
        if not route_tuple:
            raise ValueError("route advertisement batch must not be empty")

        keys = tuple(self._route_storage_key(route) for route in route_tuple)
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate route in advertisement batch")

        observed = float(time.time() if observed_at is None else observed_at)
        expires = observed + float(ttl_seconds)

        self._begin_checked()
        try:
            for route, key in zip(route_tuple, keys, strict=True):
                row = self.connection.execute(
                    """
                    SELECT
                        capabilities_json, effect_capabilities_json,
                        authorized_effects_json, available, attached, current,
                        preference, observed_at, expires_at
                    FROM portal_host_routes
                    WHERE adapter_id = ? AND route_id = ? AND node_id = ?
                      AND target_kind = ? AND target_id = ?
                    """,
                    key,
                ).fetchone()
                if row is not None:
                    existing_observed = float(row["observed_at"])
                    if observed < existing_observed:
                        raise ValueError("stale host route observation")
                    if observed == existing_observed:
                        existing = self._stored_route(route, row)
                        if (
                            existing != route
                            or float(row["expires_at"]) != expires
                        ):
                            raise ValueError(
                                "conflicting host route observation"
                            )
                        continue

                self.connection.execute(
                    """
                    INSERT INTO portal_host_routes(
                        adapter_id, route_id, node_id, target_kind, target_id,
                        capabilities_json, effect_capabilities_json,
                        authorized_effects_json, available, attached, current,
                        preference, observed_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        adapter_id, route_id, node_id, target_kind, target_id
                    ) DO UPDATE SET
                        capabilities_json = excluded.capabilities_json,
                        effect_capabilities_json = excluded.effect_capabilities_json,
                        authorized_effects_json = excluded.authorized_effects_json,
                        available = excluded.available,
                        attached = excluded.attached,
                        current = excluded.current,
                        preference = excluded.preference,
                        observed_at = excluded.observed_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        route.adapter_id,
                        route.route_id,
                        route.node_id,
                        route.target_kind,
                        route.target_id,
                        _canonical_json(route.capabilities),
                        _canonical_json(route.effect_capabilities),
                        _canonical_json(route.authorized_effects),
                        int(route.available),
                        int(route.attached),
                        int(route.current),
                        route.preference,
                        observed,
                        expires,
                    ),
                )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return route_tuple

    def advertise_route(
        self,
        route: PortalRouteAdvertisement,
        *,
        ttl_seconds: float,
        observed_at: float | None = None,
    ) -> PortalRouteAdvertisement:
        return self.advertise_routes(
            (route,),
            ttl_seconds=ttl_seconds,
            observed_at=observed_at,
        )[0]

    def active_routes(
        self,
        *,
        now: float | None = None,
    ) -> tuple[PortalRouteAdvertisement, ...]:
        observed = float(time.time() if now is None else now)
        rows = self.connection.execute(
            """
            SELECT
                adapter_id, route_id, node_id, target_kind, target_id,
                capabilities_json, effect_capabilities_json,
                authorized_effects_json, available, attached, current,
                preference
            FROM portal_host_routes
            WHERE observed_at <= ? AND expires_at > ?
            ORDER BY adapter_id, route_id, node_id, target_kind, target_id
            """,
            (observed, observed),
        ).fetchall()
        return tuple(
            PortalRouteAdvertisement(
                adapter_id=str(row["adapter_id"]),
                route_id=str(row["route_id"]),
                node_id=str(row["node_id"]),
                target_kind=str(row["target_kind"]),
                target_id=str(row["target_id"]),
                capabilities=tuple(json.loads(str(row["capabilities_json"]))),
                effect_capabilities=tuple(
                    json.loads(str(row["effect_capabilities_json"]))
                ),
                authorized_effects=tuple(
                    json.loads(str(row["authorized_effects_json"]))
                ),
                available=bool(row["available"]),
                attached=bool(row["attached"]),
                current=bool(row["current"]),
                preference=int(row["preference"]),
            )
            for row in rows
        )

    def advertise_node_occupancy(
        self,
        *,
        node_id: str,
        occupied_slots: int,
        ttl_seconds: float,
        observed_at: float | None = None,
    ) -> dict[str, object]:
        node_id = _required(node_id, "node_id")
        if type(occupied_slots) is not int or occupied_slots < 0:
            raise ValueError("occupied_slots must be a non-negative integer")
        if ttl_seconds <= 0:
            raise ValueError("host occupancy ttl_seconds must be positive")
        observed = float(time.time() if observed_at is None else observed_at)
        expires = observed + float(ttl_seconds)

        self._begin_checked()
        try:
            row = self.connection.execute(
                """
                SELECT occupied_slots, observed_at, expires_at
                FROM portal_host_node_occupancy
                WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone()
            if row is not None:
                existing_observed = float(row["observed_at"])
                existing_slots = int(row["occupied_slots"])
                if observed < existing_observed:
                    raise ValueError("stale host occupancy observation")
                if observed == existing_observed:
                    if occupied_slots != existing_slots:
                        raise ValueError(
                            "conflicting host occupancy observation"
                        )
                    self._commit_checked()
                    return {
                        "node_id": node_id,
                        "occupied_slots": existing_slots,
                        "observed_at": existing_observed,
                        "expires_at": float(row["expires_at"]),
                    }

            self.connection.execute(
                """
                INSERT INTO portal_host_node_occupancy(
                    node_id, occupied_slots, observed_at, expires_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    occupied_slots = excluded.occupied_slots,
                    observed_at = excluded.observed_at,
                    expires_at = excluded.expires_at
                """,
                (node_id, occupied_slots, observed, expires),
            )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "node_id": node_id,
            "occupied_slots": occupied_slots,
            "observed_at": observed,
            "expires_at": expires,
        }


    def advertise_snapshot(
        self,
        routes: Iterable[PortalRouteAdvertisement],
        occupancy: Mapping[str, int],
        *,
        ttl_seconds: float,
        observed_at: float | None = None,
    ) -> dict[str, object]:
        """Atomically publish one host capability/currentness snapshot."""

        if ttl_seconds <= 0:
            raise ValueError("host snapshot ttl_seconds must be positive")
        route_tuple = tuple(routes)
        occupancy_items = tuple(sorted(dict(occupancy).items()))
        if not route_tuple and not occupancy_items:
            raise ValueError("host snapshot must contain an observation")

        keys = tuple(self._route_storage_key(route) for route in route_tuple)
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate route in host snapshot")

        normalized_occupancy: list[tuple[str, int]] = []
        for node_id, occupied_slots in occupancy_items:
            node = _required(node_id, "node_id")
            if type(occupied_slots) is not int or occupied_slots < 0:
                raise ValueError(
                    "occupied_slots must be a non-negative integer"
                )
            normalized_occupancy.append((node, occupied_slots))
        if len({node_id for node_id, _ in normalized_occupancy}) != len(
            normalized_occupancy
        ):
            raise ValueError("duplicate node in host snapshot occupancy")

        observed = float(time.time() if observed_at is None else observed_at)
        expires = observed + float(ttl_seconds)

        self._begin_checked()
        try:
            for route, key in zip(route_tuple, keys, strict=True):
                row = self.connection.execute(
                    """
                    SELECT
                        capabilities_json, effect_capabilities_json,
                        authorized_effects_json, available, attached, current,
                        preference, observed_at, expires_at
                    FROM portal_host_routes
                    WHERE adapter_id = ? AND route_id = ? AND node_id = ?
                      AND target_kind = ? AND target_id = ?
                    """,
                    key,
                ).fetchone()
                if row is not None:
                    existing_observed = float(row["observed_at"])
                    if observed < existing_observed:
                        raise ValueError("stale host route observation")
                    if observed == existing_observed:
                        existing = self._stored_route(route, row)
                        if (
                            existing != route
                            or float(row["expires_at"]) != expires
                        ):
                            raise ValueError(
                                "conflicting host route observation"
                            )
                        continue

                self.connection.execute(
                    """
                    INSERT INTO portal_host_routes(
                        adapter_id, route_id, node_id, target_kind, target_id,
                        capabilities_json, effect_capabilities_json,
                        authorized_effects_json, available, attached, current,
                        preference, observed_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        adapter_id, route_id, node_id, target_kind, target_id
                    ) DO UPDATE SET
                        capabilities_json = excluded.capabilities_json,
                        effect_capabilities_json = excluded.effect_capabilities_json,
                        authorized_effects_json = excluded.authorized_effects_json,
                        available = excluded.available,
                        attached = excluded.attached,
                        current = excluded.current,
                        preference = excluded.preference,
                        observed_at = excluded.observed_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        route.adapter_id,
                        route.route_id,
                        route.node_id,
                        route.target_kind,
                        route.target_id,
                        _canonical_json(route.capabilities),
                        _canonical_json(route.effect_capabilities),
                        _canonical_json(route.authorized_effects),
                        int(route.available),
                        int(route.attached),
                        int(route.current),
                        route.preference,
                        observed,
                        expires,
                    ),
                )

            for node_id, occupied_slots in normalized_occupancy:
                row = self.connection.execute(
                    """
                    SELECT occupied_slots, observed_at, expires_at
                    FROM portal_host_node_occupancy
                    WHERE node_id = ?
                    """,
                    (node_id,),
                ).fetchone()
                if row is not None:
                    existing_observed = float(row["observed_at"])
                    if observed < existing_observed:
                        raise ValueError("stale host occupancy observation")
                    if observed == existing_observed:
                        if (
                            occupied_slots != int(row["occupied_slots"])
                            or float(row["expires_at"]) != expires
                        ):
                            raise ValueError(
                                "conflicting host occupancy observation"
                            )
                        continue

                self.connection.execute(
                    """
                    INSERT INTO portal_host_node_occupancy(
                        node_id, occupied_slots, observed_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        occupied_slots = excluded.occupied_slots,
                        observed_at = excluded.observed_at,
                        expires_at = excluded.expires_at
                    """,
                    (node_id, occupied_slots, observed, expires),
                )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise

        return {
            "routes": route_tuple,
            "occupancy": dict(normalized_occupancy),
            "observed_at": observed,
            "expires_at": expires,
        }

    def active_node_occupancy(
        self,
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        observed = float(time.time() if now is None else now)
        rows = self.connection.execute(
            """
            SELECT node_id, occupied_slots
            FROM portal_host_node_occupancy
            WHERE observed_at <= ? AND expires_at > ?
            ORDER BY node_id
            """,
            (observed, observed),
        ).fetchall()
        return {
            str(row["node_id"]): int(row["occupied_slots"])
            for row in rows
        }

    @staticmethod
    def _dispatch_payload(
        result: "PortalSessionResult",
        packet: "PortalWavePacket",
        binding: PortalRouteBinding,
    ) -> dict[str, object]:
        return {
            "schema": "PORTAL_HOST_DISPATCH_V1",
            "session_id": result.session_id,
            "wave_run_id": result.wave_run_id,
            "subject_kind": binding.subject_kind,
            "subject_id": binding.subject_id,
            "repository": packet.repository,
            "ref": packet.ref,
            "exact_head": packet.exact_head,
            "node_id": packet.node_id,
            "lane_id": packet.lane_id,
            "adapter_id": binding.adapter_id,
            "route_id": binding.route_id,
            "action": packet.action,
            "effect_ceiling": packet.effect_ceiling,
            "review_gate": packet.review_gate,
            "frontier": packet.frontier,
            "fencing_token": packet.fencing_token,
            "lineage_id": packet.lineage_id,
            "work_fingerprint": packet.work_fingerprint,
        }

    def queue_dispatch(
        self,
        *,
        result: "PortalSessionResult",
        packet: "PortalWavePacket",
        binding: PortalRouteBinding,
        queued_at: float | None = None,
    ) -> dict[str, object]:
        payload = self._dispatch_payload(result, packet, binding)
        payload_json = _canonical_json(payload)
        dispatch_id = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = float(time.time() if queued_at is None else queued_at)

        self._begin_checked()
        try:
            row = self.connection.execute(
                """
                SELECT dispatch_id, payload_json
                FROM portal_host_dispatches
                WHERE session_id = ? AND wave_run_id = ?
                  AND subject_kind = ? AND subject_id = ?
                """,
                (
                    result.session_id,
                    result.wave_run_id,
                    binding.subject_kind,
                    binding.subject_id,
                ),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_host_dispatches(
                        dispatch_id, session_id, wave_run_id,
                        subject_kind, subject_id, repository, ref, exact_head,
                        node_id, adapter_id, route_id, payload_json,
                        state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)
                    """,
                    (
                        dispatch_id,
                        result.session_id,
                        result.wave_run_id,
                        binding.subject_kind,
                        binding.subject_id,
                        packet.repository,
                        packet.ref,
                        packet.exact_head,
                        packet.node_id,
                        binding.adapter_id,
                        binding.route_id,
                        payload_json,
                        now,
                        now,
                    ),
                )
            else:
                if (
                    str(row["dispatch_id"]) != dispatch_id
                    or str(row["payload_json"]) != payload_json
                ):
                    raise ValueError(
                        "host dispatch already exists with different payload"
                    )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return self.load_dispatch(dispatch_id)

    def _row_payload(self, row: sqlite3.Row) -> dict[str, object]:
        payload = dict(json.loads(str(row["payload_json"])))
        payload.update(
            {
                "dispatch_id": str(row["dispatch_id"]),
                "state": str(row["state"]),
                "attempt_id": (
                    str(row["attempt_id"])
                    if row["attempt_id"] is not None
                    else None
                ),
                "dispatch_evidence_id": (
                    str(row["dispatch_evidence_id"])
                    if row["dispatch_evidence_id"] is not None
                    else None
                ),
                "attempted_at": (
                    float(row["attempted_at"])
                    if row["attempted_at"] is not None
                    else None
                ),
                "reconciliation_state": (
                    str(row["reconciliation_state"])
                    if row["reconciliation_state"] is not None
                    else None
                ),
                "reconciliation_evidence_id": (
                    str(row["reconciliation_evidence_id"])
                    if row["reconciliation_evidence_id"] is not None
                    else None
                ),
                "reconciled_at": (
                    float(row["reconciled_at"])
                    if row["reconciled_at"] is not None
                    else None
                ),
            }
        )
        return payload

    def _dispatch_route_is_currently_qualified(
        self,
        row: sqlite3.Row,
        *,
        now: float,
    ) -> bool:
        route_row = self.connection.execute(
            """
            SELECT
                adapter_id, route_id, node_id, target_kind, target_id,
                capabilities_json, effect_capabilities_json,
                authorized_effects_json, available, attached, current,
                preference, observed_at, expires_at
            FROM portal_host_routes
            WHERE adapter_id = ?
              AND route_id = ?
              AND node_id = ?
              AND target_kind = 'repository'
              AND target_id = ?
              AND observed_at <= ?
              AND expires_at > ?
            """,
            (
                str(row["adapter_id"]),
                str(row["route_id"]),
                str(row["node_id"]),
                str(row["repository"]),
                now,
                now,
            ),
        ).fetchone()
        if route_row is None:
            return False

        payload = json.loads(str(row["payload_json"]))
        effect_ceiling = payload.get("effect_ceiling")
        subject_kind = payload.get("subject_kind")
        subject_id = payload.get("subject_id")
        if not all(
            isinstance(value, str) and value
            for value in (
                effect_ceiling,
                subject_kind,
                subject_id,
            )
        ):
            return False

        route = PortalRouteAdvertisement(
            adapter_id=str(route_row["adapter_id"]),
            route_id=str(route_row["route_id"]),
            node_id=str(route_row["node_id"]),
            target_kind=str(route_row["target_kind"]),
            target_id=str(route_row["target_id"]),
            capabilities=tuple(
                json.loads(str(route_row["capabilities_json"]))
            ),
            effect_capabilities=tuple(
                json.loads(str(route_row["effect_capabilities_json"]))
            ),
            authorized_effects=tuple(
                json.loads(str(route_row["authorized_effects_json"]))
            ),
            available=bool(route_row["available"]),
            attached=bool(route_row["attached"]),
            current=bool(route_row["current"]),
            preference=int(route_row["preference"]),
        )
        request = PortalRouteRequest(
            subject_kind=subject_kind,
            subject_id=subject_id,
            node_id=str(row["node_id"]),
            target_kind="repository",
            target_id=str(row["repository"]),
            required_capabilities=("semantic_work",),
            required_effect=effect_ceiling,
            preferred_adapter_id=str(row["adapter_id"]),
            preferred_route_id=str(row["route_id"]),
        )
        try:
            resolve_portal_route(request, (route,))
        except ValueError:
            return False
        return True

    def load_dispatch(self, dispatch_id: str) -> dict[str, object]:
        dispatch_id = _required(dispatch_id, "dispatch_id")
        row = self.connection.execute(
            """
            SELECT *
            FROM portal_host_dispatches
            WHERE dispatch_id = ?
            """,
            (dispatch_id,),
        ).fetchone()
        if row is None:
            raise ValueError("host dispatch not found")
        return self._row_payload(row)

    def pending_dispatches(
        self,
        *,
        session_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        if session_id is None:
            rows = self.connection.execute(
                """
                SELECT *
                FROM portal_host_dispatches
                WHERE state = 'QUEUED'
                ORDER BY created_at, dispatch_id
                """
            ).fetchall()
        else:
            session_id = _required(session_id, "session_id")
            rows = self.connection.execute(
                """
                SELECT *
                FROM portal_host_dispatches
                WHERE state = 'QUEUED' AND session_id = ?
                ORDER BY created_at, dispatch_id
                """,
                (session_id,),
            ).fetchall()
        return tuple(self._row_payload(row) for row in rows)

    def pending_dispatch_diagnostics(
        self,
        *,
        session_id: str | None = None,
        now: float | None = None,
    ) -> tuple[dict[str, object], ...]:
        observed = float(time.time() if now is None else now)
        parameters: tuple[object, ...] = ()
        session_clause = ""
        if session_id is not None:
            session_id = _required(session_id, "session_id")
            session_clause = " AND session_id = ?"
            parameters = (session_id,)

        rows = self.connection.execute(
            """
            SELECT *
            FROM portal_host_dispatches
            WHERE state = 'QUEUED'
            """ + session_clause + """
            ORDER BY created_at, dispatch_id
            """,
            parameters,
        ).fetchall()

        diagnostics: list[dict[str, object]] = []
        for row in rows:
            payload = self._row_payload(row)
            payload["route_qualified"] = (
                self._dispatch_route_is_currently_qualified(
                    row,
                    now=observed,
                )
            )
            diagnostics.append(payload)
        return tuple(diagnostics)

    def take_pending_dispatch(
        self,
        *,
        adapter_ids: Iterable[str],
        attempt_id: str,
        evidence_id: str,
        session_id: str | None = None,
        attempted_at: float | None = None,
    ) -> dict[str, object] | None:
        normalized = tuple(
            sorted(
                {
                    _required(adapter_id, "adapter_id")
                    for adapter_id in adapter_ids
                }
            )
        )
        if not normalized:
            raise ValueError("adapter_ids must contain at least one adapter")

        attempt_id = _required(attempt_id, "attempt_id")
        evidence_id = _required(evidence_id, "dispatch evidence_id")
        now = float(time.time() if attempted_at is None else attempted_at)

        clauses = ["state = 'QUEUED'"]
        parameters: list[object] = []
        if session_id is not None:
            session_id = _required(session_id, "session_id")
            clauses.append("session_id = ?")
            parameters.append(session_id)
        placeholders = ", ".join("?" for _ in normalized)
        clauses.append(f"adapter_id IN ({placeholders})")
        parameters.extend(normalized)

        self._begin_checked()
        try:
            rows = self.connection.execute(
                """
                SELECT *
                FROM portal_host_dispatches
                WHERE """ + " AND ".join(clauses) + """
                ORDER BY created_at, dispatch_id
                """,
                tuple(parameters),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if self._dispatch_route_is_currently_qualified(
                        candidate,
                        now=now,
                    )
                ),
                None,
            )
            if row is None:
                self._commit_checked()
                return None

            dispatch_id = str(row["dispatch_id"])
            cursor = self.connection.execute(
                """
                UPDATE portal_host_dispatches
                SET state = 'ATTEMPTED',
                    attempt_id = ?,
                    dispatch_evidence_id = ?,
                    attempted_at = ?,
                    updated_at = ?
                WHERE dispatch_id = ? AND state = 'QUEUED'
                """,
                (
                    attempt_id,
                    evidence_id,
                    now,
                    now,
                    dispatch_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "host dispatch changed before attempt boundary"
                )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return self.load_dispatch(dispatch_id)

    def unresolved_dispatches(
        self,
        *,
        session_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        parameters: tuple[object, ...] = ()
        session_clause = ""
        if session_id is not None:
            session_id = _required(session_id, "session_id")
            session_clause = " AND session_id = ?"
            parameters = (session_id,)

        rows = self.connection.execute(
            """
            SELECT *
            FROM portal_host_dispatches
            WHERE state = 'ATTEMPTED'
              AND (
                    reconciliation_state IS NULL
                    OR reconciliation_state NOT IN (
                        'VERIFIED_COMPLETE',
                        'VERIFIED_HELD'
                    )
              )
            """ + session_clause + """
            ORDER BY created_at, dispatch_id
            """,
            parameters,
        ).fetchall()
        return tuple(self._row_payload(row) for row in rows)

    def find_dispatch(
        self,
        *,
        wave_run_id: str,
        subject_kind: str,
        subject_id: str,
    ) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM portal_host_dispatches
            WHERE wave_run_id = ? AND subject_kind = ? AND subject_id = ?
            """,
            (
                _required(wave_run_id, "wave_run_id"),
                _required(subject_kind, "subject_kind"),
                _required(subject_id, "subject_id"),
            ),
        ).fetchone()
        return self._row_payload(row) if row is not None else None

    def mark_attempted(
        self,
        *,
        dispatch_id: str,
        attempt_id: str,
        evidence_id: str,
        attempted_at: float | None = None,
    ) -> dict[str, object]:
        dispatch_id = _required(dispatch_id, "dispatch_id")
        attempt_id = _required(attempt_id, "attempt_id")
        evidence_id = _required(evidence_id, "dispatch evidence_id")
        now = float(time.time() if attempted_at is None else attempted_at)

        self._begin_checked()
        try:
            row = self.connection.execute(
                """
                SELECT *
                FROM portal_host_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise ValueError("host dispatch not found")
            state = str(row["state"])
            if state == "ATTEMPTED":
                if (
                    str(row["attempt_id"]) != attempt_id
                    or str(row["dispatch_evidence_id"]) != evidence_id
                ):
                    raise ValueError(
                        "host dispatch already crossed effect boundary "
                        "with a different attempt"
                    )
                self._commit_checked()
                return self.load_dispatch(dispatch_id)
            if state != "QUEUED":
                raise ValueError("host dispatch cannot be attempted")
            if not self._dispatch_route_is_currently_qualified(
                row,
                now=now,
            ):
                raise ValueError(
                    "host route is not currently qualified for attempt"
                )

            self.connection.execute(
                """
                UPDATE portal_host_dispatches
                SET state = 'ATTEMPTED',
                    attempt_id = ?,
                    dispatch_evidence_id = ?,
                    attempted_at = ?,
                    updated_at = ?
                WHERE dispatch_id = ?
                """,
                (attempt_id, evidence_id, now, now, dispatch_id),
            )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return self.load_dispatch(dispatch_id)

    def record_reconciliation(
        self,
        *,
        dispatch_id: str,
        state: str,
        evidence_id: str,
        reconciled_at: float | None = None,
    ) -> dict[str, object]:
        dispatch_id = _required(dispatch_id, "dispatch_id")
        state = _required(state, "reconciliation state")
        evidence_id = _required(evidence_id, "reconciliation evidence_id")
        if state not in _ALLOWED_RECONCILIATION_STATES:
            raise ValueError("unsupported host reconciliation state")
        now = float(time.time() if reconciled_at is None else reconciled_at)

        self._begin_checked()
        try:
            row = self.connection.execute(
                """
                SELECT
                    state, reconciliation_state,
                    reconciliation_evidence_id
                FROM portal_host_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise ValueError("host dispatch not found")

            dispatch_state = str(row["state"])
            existing_state = (
                str(row["reconciliation_state"])
                if row["reconciliation_state"] is not None
                else None
            )
            existing_evidence = (
                str(row["reconciliation_evidence_id"])
                if row["reconciliation_evidence_id"] is not None
                else None
            )
            if existing_state in _FINAL_RECONCILIATION_STATES:
                if existing_state == state and existing_evidence == evidence_id:
                    self._commit_checked()
                    return self.load_dispatch(dispatch_id)
                raise ValueError("host dispatch already has final reconciliation")

            if (
                state in {"IN_PROGRESS", "OUTCOME_UNKNOWN", "VERIFIED_COMPLETE"}
                and dispatch_state != "ATTEMPTED"
            ):
                raise ValueError(
                    "host dispatch must be attempted before reconciliation"
                )

            self.connection.execute(
                """
                UPDATE portal_host_dispatches
                SET reconciliation_state = ?,
                    reconciliation_evidence_id = ?,
                    reconciled_at = ?,
                    updated_at = ?
                WHERE dispatch_id = ?
                """,
                (state, evidence_id, now, now, dispatch_id),
            )
            self._commit_checked()
        except BaseException:
            self.connection.rollback()
            raise
        return self.load_dispatch(dispatch_id)


class PortalHostNodeCurrentness:
    """Fail-closed node occupancy supplied by the external execution host."""

    def __init__(
        self,
        *,
        store: PortalHostBridgeStore,
        required_node_ids: Iterable[str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        required: set[str] = set()
        for raw_node_id in required_node_ids:
            node_id = _required(raw_node_id, "required node_id")
            if node_id in required:
                raise ValueError(f"duplicate required host occupancy node: {node_id}")
            required.add(node_id)
        self.store = store
        self.required_node_ids = tuple(sorted(required))
        self.clock = clock

    def __call__(self) -> dict[str, int]:
        active = self.store.active_node_occupancy(now=float(self.clock()))
        missing = [
            node_id
            for node_id in self.required_node_ids
            if node_id not in active
        ]
        if missing:
            raise ValueError(
                "missing current host occupancy for node: "
                + ", ".join(missing)
            )
        return {
            node_id: active[node_id]
            for node_id in self.required_node_ids
        }


class PortalHostExecutionAdapter:
    """Execution adapter whose external effect is performed by the chat host."""

    def __init__(
        self,
        *,
        store: PortalHostBridgeStore,
        request_builder: Callable[["PortalWavePacket"], PortalRouteRequest] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.request_builder = request_builder or self._default_request
        self.clock = clock

    @staticmethod
    def _default_request(packet: "PortalWavePacket") -> PortalRouteRequest:
        return PortalRouteRequest(
            subject_kind="repository",
            subject_id=packet.subject_id,
            node_id=packet.node_id,
            target_kind="repository",
            target_id=packet.repository,
            required_capabilities=("semantic_work",),
            required_effect=packet.effect_ceiling,
        )

    def select_routes(
        self,
        result: "PortalSessionResult",
    ) -> tuple[PortalRouteBinding, ...]:
        advertisements = self.store.active_routes(now=float(self.clock()))
        bindings: list[PortalRouteBinding] = []
        for packet in result.packets:
            request = self.request_builder(packet)
            if (
                request.subject_kind != "repository"
                or request.subject_id != packet.subject_id
                or request.node_id != packet.node_id
                or request.target_kind != "repository"
                or request.target_id != packet.repository
            ):
                raise ValueError("host route request does not match admitted packet")
            bindings.append(resolve_portal_route(request, advertisements))
        return tuple(bindings)

    def dispatch(
        self,
        result: "PortalSessionResult",
        routes: tuple[PortalRouteBinding, ...],
    ) -> tuple[PortalDispatchRecord, ...]:
        packets = {packet.subject_id: packet for packet in result.packets}
        if len(packets) != len(result.packets):
            raise ValueError("duplicate admitted packet subject")
        seen: set[str] = set()
        records: list[PortalDispatchRecord] = []
        for binding in routes:
            if binding.subject_kind != "repository":
                raise ValueError("host bridge only supports repository subjects")
            if binding.subject_id in seen:
                raise ValueError("duplicate host dispatch route subject")
            seen.add(binding.subject_id)
            packet = packets.get(binding.subject_id)
            if packet is None:
                raise ValueError("host dispatch route references non-admitted subject")
            queued = self.store.queue_dispatch(
                result=result,
                packet=packet,
                binding=binding,
                queued_at=float(self.clock()),
            )
            dispatch_id = str(queued["dispatch_id"])
            records.append(
                PortalDispatchRecord(
                    subject_kind=binding.subject_kind,
                    subject_id=binding.subject_id,
                    adapter_id=binding.adapter_id,
                    route_id=binding.route_id,
                    state="HOST_QUEUED",
                    evidence_id="host-dispatch:" + dispatch_id,
                )
            )
        if set(seen) != set(packets):
            raise ValueError("host dispatch routes do not cover admitted packets")
        return tuple(records)

    def reconcile(
        self,
        status: Mapping[str, object],
        subjects: Iterable[Mapping[str, object]] | None = None,
    ) -> tuple[PortalReconciliationRecord, ...]:
        if subjects is None:
            raw_subjects = status.get("subjects")
            if not isinstance(raw_subjects, list):
                raise ValueError("Portal status subjects must be a list")
            candidates = tuple(
                raw
                for raw in raw_subjects
                if isinstance(raw, Mapping) and raw.get("state") == "ACTIVE"
            )
        else:
            candidates = tuple(subjects)

        records: list[PortalReconciliationRecord] = []
        for raw in candidates:
            subject_kind = raw.get("subject_kind")
            subject_id = raw.get("subject_id")
            wave_run_id = raw.get("wave_run_id")
            adapter_id = raw.get("adapter_id")
            route_id = raw.get("route_id")
            if not all(
                isinstance(value, str) and value
                for value in (
                    subject_kind,
                    subject_id,
                    wave_run_id,
                    adapter_id,
                    route_id,
                )
            ):
                continue

            dispatch = self.store.find_dispatch(
                wave_run_id=wave_run_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
            )
            if dispatch is None:
                continue
            if (
                dispatch["adapter_id"] != adapter_id
                or dispatch["route_id"] != route_id
            ):
                raise ValueError(
                    "host reconciliation differs from durable route binding"
                )

            reconciliation_state = dispatch.get("reconciliation_state")
            reconciliation_evidence = dispatch.get(
                "reconciliation_evidence_id"
            )
            if isinstance(reconciliation_state, str) and reconciliation_state:
                if not (
                    isinstance(reconciliation_evidence, str)
                    and reconciliation_evidence
                ):
                    raise ValueError(
                        "host reconciliation is missing durable evidence"
                    )
                records.append(
                    PortalReconciliationRecord(
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state=reconciliation_state,
                        evidence_id=reconciliation_evidence,
                    )
                )
                continue

            if dispatch["state"] == "ATTEMPTED":
                evidence = dispatch.get("dispatch_evidence_id")
                if not isinstance(evidence, str) or not evidence:
                    raise ValueError(
                        "attempted host dispatch is missing durable evidence"
                    )
                records.append(
                    PortalReconciliationRecord(
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        adapter_id=adapter_id,
                        route_id=route_id,
                        state="OUTCOME_UNKNOWN",
                        evidence_id=evidence,
                    )
                )
        return tuple(records)
