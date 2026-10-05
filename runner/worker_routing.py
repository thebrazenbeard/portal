from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import (
    Frontier,
    InvocationRoute,
    ProjectDefinition,
    ReplayPolicy,
    RouteEffectClass,
    RouteState,
    WorkerDefinition,
    WorkerLifecycle,
)


@dataclass(frozen=True)
class ReadOnlyWorkerRoute:
    worker_id: str
    invocation_route: InvocationRoute
    replay_policy: ReplayPolicy


@dataclass(frozen=True)
class WorkerRouteDeliveryClaim:
    route_id: str
    worker_id: str
    invocation_route: InvocationRoute
    fencing_token: int
    expires_at: float
    payload: dict[str, object]


@dataclass(frozen=True)
class WorkerRouteReceipt:
    route_id: str
    worker_id: str
    invocation_route: InvocationRoute
    fencing_token: int
    receipt_class: str
    receipt_sha256: str
    state: str


@dataclass(frozen=True)
class WorkerRouteEnvelope:
    route_id: str
    snapshot_id: int
    frontier_fingerprint: str
    queue_fencing_token: int
    worker_registry_digest: str
    packet_sensitivity: str
    worker_id: str
    invocation_route: InvocationRoute
    replay_policy: ReplayPolicy
    state: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_route_outbox (
    route_id TEXT PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    frontier_fingerprint TEXT NOT NULL,
    queue_fencing_token INTEGER NOT NULL,
    worker_registry_digest TEXT NOT NULL,
    packet_sensitivity TEXT NOT NULL DEFAULT 'PRIVATE_UNKNOWN',
    worker_id TEXT NOT NULL,
    invocation_route TEXT NOT NULL,
    replay_policy TEXT NOT NULL,
    target_repository TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    target_head TEXT NOT NULL,
    frontier_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    delivery_holder TEXT,
    delivery_fencing_token INTEGER NOT NULL DEFAULT 0,
    delivery_expires_at REAL NOT NULL DEFAULT 0,
    receipt_class TEXT,
    receipt_sha256 TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(snapshot_id, frontier_fingerprint, queue_fencing_token)
);
"""


def validate_read_only_delivery_route(
    *,
    workers: Iterable[WorkerDefinition],
    worker_id: str,
    invocation_route: InvocationRoute,
) -> ReplayPolicy:
    matches = tuple(worker for worker in workers if worker.id == worker_id)
    if len(matches) != 1:
        raise ValueError("delivery worker is absent or ambiguous")
    worker = matches[0]
    if worker.lifecycle is not WorkerLifecycle.EXECUTABLE:
        raise ValueError("delivery worker is not EXECUTABLE")
    if worker.routes.get(invocation_route) is not RouteState.VERIFIED:
        raise ValueError("delivery worker route is not VERIFIED")
    contract = worker.route_contracts.get(invocation_route)
    if contract is None:
        raise ValueError("delivery worker route lacks an effect contract")
    if contract.effect_class is not RouteEffectClass.READ_ONLY:
        raise ValueError("delivery worker route is not qualified as READ_ONLY")
    return contract.replay_policy


def resolve_read_only_worker_route(
    *,
    project: ProjectDefinition,
    frontier: Frontier,
    workers: Iterable[WorkerDefinition],
) -> ReadOnlyWorkerRoute | None:
    target = next(
        (
            item
            for item in project.execution_targets
            if item.work_type == frontier.work_type
        ),
        None,
    )
    if target is None:
        raise ValueError("frontier lacks an explicit execution target")
    if target.worker_id is None or target.worker_route is None:
        if frontier.work_type == "INSPECT":
            return None
        raise ValueError(
            "non-INSPECT read-only work requires an explicit worker route"
        )

    replay_policy = validate_read_only_delivery_route(
        workers=workers,
        worker_id=target.worker_id,
        invocation_route=target.worker_route,
    )
    return ReadOnlyWorkerRoute(
        worker_id=target.worker_id,
        invocation_route=target.worker_route,
        replay_policy=replay_policy,
    )


def worker_route_ready(
    *,
    project: ProjectDefinition,
    frontier: Frontier,
    workers: Iterable[WorkerDefinition],
) -> bool:
    try:
        route = resolve_read_only_worker_route(
            project=project,
            frontier=frontier,
            workers=workers,
        )
    except ValueError:
        return False
    return frontier.work_type == "INSPECT" or route is not None


def _route_payload(
    *,
    snapshot_id: int,
    frontier_fingerprint: str,
    queue_fencing_token: int,
    worker_registry_digest: str,
    packet_sensitivity: str,
    route: ReadOnlyWorkerRoute,
    target_repository: str,
    target_ref: str,
    target_head: str,
    frontier_json: str,
) -> dict[str, object]:
    return {
        "snapshot_id": snapshot_id,
        "frontier_fingerprint": frontier_fingerprint,
        "queue_fencing_token": queue_fencing_token,
        "worker_registry_digest": worker_registry_digest,
        "packet_sensitivity": packet_sensitivity,
        "worker_id": route.worker_id,
        "invocation_route": route.invocation_route.value,
        "replay_policy": route.replay_policy.value,
        "target_repository": target_repository,
        "target_ref": target_ref,
        "target_head": target_head,
        "frontier_json": json.loads(frontier_json),
    }


def ensure_worker_route_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(worker_route_outbox)")
    }
    migrations = {
        "packet_sensitivity": "TEXT NOT NULL DEFAULT 'PRIVATE_UNKNOWN'",
        "delivery_holder": "TEXT",
        "delivery_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        "delivery_expires_at": "REAL NOT NULL DEFAULT 0",
        "receipt_class": "TEXT",
        "receipt_sha256": "TEXT",
    }
    for name, sql_type in migrations.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE worker_route_outbox ADD COLUMN {name} {sql_type}"
            )


def enqueue_worker_route_record(
    connection: sqlite3.Connection,
    *,
    snapshot_id: int,
    frontier_fingerprint: str,
    queue_fencing_token: int,
    worker_registry_digest: str,
    packet_sensitivity: str,
    route: ReadOnlyWorkerRoute,
    target_repository: str,
    target_ref: str,
    target_head: str,
    frontier_json: str,
    now: float,
) -> WorkerRouteEnvelope:
    if packet_sensitivity not in {"PUBLIC", "PRIVATE"}:
        raise ValueError("worker packet sensitivity must be PUBLIC or PRIVATE")
    payload = _route_payload(
        snapshot_id=snapshot_id,
        frontier_fingerprint=frontier_fingerprint,
        queue_fencing_token=queue_fencing_token,
        worker_registry_digest=worker_registry_digest,
        packet_sensitivity=packet_sensitivity,
        route=route,
        target_repository=target_repository,
        target_ref=target_ref,
        target_head=target_head,
        frontier_json=frontier_json,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    route_id = f"worker-route-{digest[:24]}"

    existing = connection.execute(
        """
        SELECT route_id, payload_sha256, state
        FROM worker_route_outbox
        WHERE snapshot_id = ?
          AND frontier_fingerprint = ?
          AND queue_fencing_token = ?
        """,
        (snapshot_id, frontier_fingerprint, queue_fencing_token),
    ).fetchone()
    if existing is not None:
        if str(existing[1]) != digest:
            raise ValueError(
                "worker route identity already exists with different payload"
            )
        return WorkerRouteEnvelope(
            route_id=str(existing[0]),
            snapshot_id=snapshot_id,
            frontier_fingerprint=frontier_fingerprint,
            queue_fencing_token=queue_fencing_token,
            worker_registry_digest=worker_registry_digest,
            packet_sensitivity=packet_sensitivity,
            worker_id=route.worker_id,
            invocation_route=route.invocation_route,
            replay_policy=route.replay_policy,
            state=str(existing[2]),
        )

    connection.execute(
        """
        INSERT INTO worker_route_outbox(
            route_id, snapshot_id, frontier_fingerprint,
            queue_fencing_token, worker_registry_digest, packet_sensitivity,
            worker_id, invocation_route, replay_policy,
            target_repository, target_ref, target_head,
            frontier_json, payload_sha256, state,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """,
        (
            route_id,
            snapshot_id,
            frontier_fingerprint,
            queue_fencing_token,
            worker_registry_digest,
            packet_sensitivity,
            route.worker_id,
            route.invocation_route.value,
            route.replay_policy.value,
            target_repository,
            target_ref,
            target_head,
            frontier_json,
            digest,
            now,
            now,
        ),
    )
    return WorkerRouteEnvelope(
        route_id=route_id,
        snapshot_id=snapshot_id,
        frontier_fingerprint=frontier_fingerprint,
        queue_fencing_token=queue_fencing_token,
        worker_registry_digest=worker_registry_digest,
        packet_sensitivity=packet_sensitivity,
        worker_id=route.worker_id,
        invocation_route=route.invocation_route,
        replay_policy=route.replay_policy,
        state="PENDING",
    )


class SqliteWorkerRouteStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        ensure_worker_route_schema(self.connection)

    def close(self) -> None:
        self.connection.close()

    def enqueue(
        self,
        *,
        snapshot_id: int,
        frontier_fingerprint: str,
        queue_fencing_token: int,
        worker_registry_digest: str,
        route: ReadOnlyWorkerRoute,
        target_repository: str,
        target_ref: str,
        target_head: str,
        frontier_json: str,
        now: float,
        packet_sensitivity: str = "PUBLIC",
    ) -> WorkerRouteEnvelope:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            envelope = enqueue_worker_route_record(
                self.connection,
                snapshot_id=snapshot_id,
                frontier_fingerprint=frontier_fingerprint,
                queue_fencing_token=queue_fencing_token,
                worker_registry_digest=worker_registry_digest,
                packet_sensitivity=packet_sensitivity,
                route=route,
                target_repository=target_repository,
                target_ref=target_ref,
                target_head=target_head,
                frontier_json=frontier_json,
                now=now,
            )
            self.connection.commit()
            return envelope
        except BaseException:
            self.connection.rollback()
            raise

    def _provider_subject_current(
        self,
        *,
        snapshot_id: int,
        frontier_json: str,
    ) -> bool:
        snapshot = self.connection.execute(
            """
            SELECT dependency_digest
            FROM portfolio_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError("worker route snapshot is absent")
        dependency_digest = str(snapshot[0])
        latest = self.connection.execute(
            """
            SELECT snapshot_id
            FROM portfolio_snapshots
            WHERE dependency_digest = ?
            ORDER BY snapshot_id DESC
            LIMIT 1
            """,
            (dependency_digest,),
        ).fetchone()
        if latest is None:
            return False

        raw = json.loads(frontier_json)
        if not isinstance(raw, dict):
            raise ValueError("worker route frontier payload is structurally invalid")
        frontier = Frontier.from_mapping(raw)
        current_subjects = {
            (
                str(repository),
                str(ref),
                str(commit_sha),
                str(path) or None,
                None if digest is None else str(digest),
            )
            for repository, ref, path, commit_sha, digest
            in self.connection.execute(
                """
                SELECT repository, ref, path, commit_sha, digest
                FROM portfolio_observations
                WHERE snapshot_id = ?
                """,
                (int(latest[0]),),
            ).fetchall()
        }
        return frontier.subject.identity() in current_subjects

    def _supersede_stale_route(
        self,
        *,
        route_id: str,
        snapshot_id: int,
        frontier_fingerprint: str,
        queue_fencing_token: int,
        now: float,
    ) -> None:
        self.connection.execute(
            """
            UPDATE worker_route_outbox
            SET state = 'SUPERSEDED',
                delivery_expires_at = 0,
                updated_at = ?
            WHERE route_id = ?
            """,
            (now, route_id),
        )
        has_queue_table = self.connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'portfolio_queue_claims'
            """
        ).fetchone() is not None
        if has_queue_table:
            self.connection.execute(
                """
                UPDATE portfolio_queue_claims
                SET state = 'SUPERSEDED',
                    holder = NULL,
                    expires_at = 0,
                    reason = ?,
                    updated_at = ?
                WHERE snapshot_id = ?
                  AND frontier_fingerprint = ?
                  AND fencing_token = ?
                  AND state = 'ROUTED'
                """,
                (
                    "worker route provider subject is no longer current",
                    now,
                    snapshot_id,
                    frontier_fingerprint,
                    queue_fencing_token,
                ),
            )

    def claim_next(
        self,
        *,
        worker_id: str,
        invocation_route: InvocationRoute,
        workers: Iterable[WorkerDefinition],
        holder: str,
        now: float,
        ttl: float,
        worker_registry_digest: str,
        allow_private: bool = True,
    ) -> WorkerRouteDeliveryClaim | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if not holder.strip():
            raise ValueError("delivery holder is required")
        if ttl <= 0:
            raise ValueError("delivery lease ttl must be positive")
        replay_policy = validate_read_only_delivery_route(
            workers=workers,
            worker_id=worker_id,
            invocation_route=invocation_route,
        )

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                """
                SELECT
                    route_id, state, delivery_holder,
                    delivery_fencing_token, delivery_expires_at,
                    worker_registry_digest, target_repository, target_ref,
                    target_head, frontier_json, snapshot_id,
                    frontier_fingerprint, queue_fencing_token,
                    replay_policy, packet_sensitivity
                FROM worker_route_outbox
                WHERE worker_id = ? AND invocation_route = ?
                  AND state IN ('PENDING', 'CLAIMED')
                ORDER BY created_at, route_id
                """,
                (worker_id, invocation_route.value),
            ).fetchall()
            for row in rows:
                state = str(row[1])
                if str(row[5]) != worker_registry_digest:
                    continue
                if str(row[13]) != replay_policy.value:
                    raise ValueError(
                        "worker route replay policy diverges from current registry"
                    )
                packet_sensitivity = str(row[14])
                if packet_sensitivity not in {"PUBLIC", "PRIVATE"}:
                    raise ValueError(
                        "worker packet sensitivity is missing or invalid"
                    )
                if packet_sensitivity == "PRIVATE" and not allow_private:
                    continue
                if not self._provider_subject_current(
                    snapshot_id=int(row[10]),
                    frontier_json=str(row[9]),
                ):
                    self._supersede_stale_route(
                        route_id=str(row[0]),
                        snapshot_id=int(row[10]),
                        frontier_fingerprint=str(row[11]),
                        queue_fencing_token=int(row[12]),
                        now=now,
                    )
                    continue
                if state == "CLAIMED" and now < float(row[4]):
                    continue
                if (
                    state == "CLAIMED"
                    and replay_policy is not ReplayPolicy.SAFE
                ):
                    self.connection.execute(
                        """
                        UPDATE worker_route_outbox
                        SET state = 'OUTCOME_UNKNOWN',
                            delivery_expires_at = 0,
                            updated_at = ?
                        WHERE route_id = ?
                        """,
                        (now, str(row[0])),
                    )
                    continue

                token = int(row[3]) + 1
                expires_at = now + ttl
                route_id = str(row[0])
                self.connection.execute(
                    """
                    UPDATE worker_route_outbox
                    SET state = 'CLAIMED',
                        delivery_holder = ?,
                        delivery_fencing_token = ?,
                        delivery_expires_at = ?,
                        updated_at = ?
                    WHERE route_id = ?
                    """,
                    (holder, token, expires_at, now, route_id),
                )
                payload = {
                    "route_id": route_id,
                    "snapshot_id": int(row[10]),
                    "frontier_fingerprint": str(row[11]),
                    "queue_fencing_token": int(row[12]),
                    "worker_registry_digest": str(row[5]),
                    "packet_sensitivity": packet_sensitivity,
                    "worker_id": worker_id,
                    "invocation_route": invocation_route.value,
                    "replay_policy": str(row[13]),
                    "target_repository": str(row[6]),
                    "target_ref": str(row[7]),
                    "target_head": str(row[8]),
                    "frontier": json.loads(str(row[9])),
                }
                self.connection.commit()
                return WorkerRouteDeliveryClaim(
                    route_id=route_id,
                    worker_id=worker_id,
                    invocation_route=invocation_route,
                    fencing_token=token,
                    expires_at=expires_at,
                    payload=payload,
                )
            self.connection.commit()
            return None
        except BaseException:
            self.connection.rollback()
            raise

    def record_receipt(
        self,
        *,
        route_id: str,
        worker_id: str,
        invocation_route: InvocationRoute,
        holder: str,
        expected_fencing_token: int,
        receipt_class: str,
        receipt_sha256: str,
        now: float,
    ) -> WorkerRouteReceipt:
        allowed = {
            "SUCCEEDED",
            "FAILED_RETRYABLE",
            "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN",
            "SUPERSEDED",
        }
        if receipt_class not in allowed:
            raise ValueError("unsupported worker receipt class")
        if len(receipt_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in receipt_sha256
        ):
            raise ValueError("worker receipt digest must be lowercase SHA-256")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT
                    worker_id, invocation_route, state,
                    delivery_holder, delivery_fencing_token,
                    delivery_expires_at, receipt_class, receipt_sha256
                FROM worker_route_outbox
                WHERE route_id = ?
                """,
                (route_id,),
            ).fetchone()
            if row is None:
                raise ValueError("worker route does not exist")
            if str(row[0]) != worker_id or str(row[1]) != invocation_route.value:
                raise ValueError("worker receipt route identity mismatch")
            if (
                str(row[2]) == "RECEIPT_RECORDED"
                and row[6] is not None
                and row[7] is not None
            ):
                if (
                    str(row[3]) == holder
                    and str(row[6]) == receipt_class
                    and str(row[7]) == receipt_sha256
                    and int(row[4]) == expected_fencing_token
                ):
                    self.connection.commit()
                    return WorkerRouteReceipt(
                        route_id=route_id,
                        worker_id=worker_id,
                        invocation_route=invocation_route,
                        fencing_token=expected_fencing_token,
                        receipt_class=receipt_class,
                        receipt_sha256=receipt_sha256,
                        state="RECEIPT_RECORDED",
                    )
                raise ValueError("worker route already has a different receipt")
            if (
                str(row[2]) != "CLAIMED"
                or str(row[3]) != holder
                or int(row[4]) != expected_fencing_token
                or now >= float(row[5])
            ):
                raise ValueError("delivery fence no longer authorizes receipt")
            self.connection.execute(
                """
                UPDATE worker_route_outbox
                SET state = 'RECEIPT_RECORDED',
                    receipt_class = ?,
                    receipt_sha256 = ?,
                    delivery_expires_at = 0,
                    updated_at = ?
                WHERE route_id = ?
                """,
                (receipt_class, receipt_sha256, now, route_id),
            )
            self.connection.commit()
            return WorkerRouteReceipt(
                route_id=route_id,
                worker_id=worker_id,
                invocation_route=invocation_route,
                fencing_token=expected_fencing_token,
                receipt_class=receipt_class,
                receipt_sha256=receipt_sha256,
                state="RECEIPT_RECORDED",
            )
        except BaseException:
            self.connection.rollback()
            raise

    def summary(self) -> dict[str, object]:
        rows = self.connection.execute(
            """
            SELECT state, COUNT(*)
            FROM worker_route_outbox
            GROUP BY state
            ORDER BY state
            """
        ).fetchall()
        states = {str(state): int(count) for state, count in rows}
        return {"routes": sum(states.values()), "states": states}


def summarize_worker_routes(state_db: Path) -> dict[str, object]:
    state_db = Path(state_db)
    if not state_db.exists():
        return {"routes": 0, "states": {}}
    store = SqliteWorkerRouteStore(state_db)
    try:
        return store.summary()
    finally:
        store.close()
