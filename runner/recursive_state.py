from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any, Collection, Mapping

from .leases import Lease
from .models import ExactSubject
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


_TERMINAL_STATUSES = frozenset(
    {
        WorkUnitStatus.COMPLETE,
        WorkUnitStatus.FAILED_DETERMINISTIC,
        WorkUnitStatus.SUPERSEDED,
    }
)

_LEASE_BOUND_NONTERMINAL_STATUSES = frozenset(
    {
        WorkUnitStatus.CLAIMED,
        WorkUnitStatus.RUNNING,
        WorkUnitStatus.VERIFYING,
        WorkUnitStatus.FAILED_RETRYABLE,
        WorkUnitStatus.OUTCOME_UNKNOWN,
    }
)

_ALLOWED_STATUS_TRANSITIONS = {
    WorkUnitStatus.PENDING: frozenset(
        {WorkUnitStatus.CLAIMED, WorkUnitStatus.RUNNING}
    ),
    WorkUnitStatus.CLAIMED: frozenset(
        {
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.FAILED_RETRYABLE,
            WorkUnitStatus.FAILED_DETERMINISTIC,
            WorkUnitStatus.OUTCOME_UNKNOWN,
            WorkUnitStatus.SUPERSEDED,
        }
    ),
    WorkUnitStatus.RUNNING: frozenset(
        {
            WorkUnitStatus.VERIFYING,
            WorkUnitStatus.FAILED_RETRYABLE,
            WorkUnitStatus.FAILED_DETERMINISTIC,
            WorkUnitStatus.OUTCOME_UNKNOWN,
            WorkUnitStatus.SUPERSEDED,
        }
    ),
    WorkUnitStatus.VERIFYING: frozenset(
        {
            WorkUnitStatus.COMPLETE,
            WorkUnitStatus.FAILED_RETRYABLE,
            WorkUnitStatus.FAILED_DETERMINISTIC,
            WorkUnitStatus.OUTCOME_UNKNOWN,
            WorkUnitStatus.SUPERSEDED,
        }
    ),
    WorkUnitStatus.FAILED_RETRYABLE: frozenset(
        {WorkUnitStatus.CLAIMED, WorkUnitStatus.RUNNING}
    ),
    WorkUnitStatus.OUTCOME_UNKNOWN: frozenset(
        {
            WorkUnitStatus.CLAIMED,
            WorkUnitStatus.RUNNING,
            WorkUnitStatus.VERIFYING,
        }
    ),
    WorkUnitStatus.COMPLETE: frozenset(),
    WorkUnitStatus.FAILED_DETERMINISTIC: frozenset(),
    WorkUnitStatus.SUPERSEDED: frozenset(),
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS recursive_work_state (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    work_json TEXT NOT NULL,
    budget_scope_id TEXT NOT NULL,
    parent_fingerprint TEXT,
    ancestry_json TEXT NOT NULL,
    effective_capabilities_json TEXT NOT NULL,
    immutable_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    generation INTEGER NOT NULL,
    PRIMARY KEY (lineage_id, work_fingerprint)
);
"""


@dataclass(frozen=True)
class StoredRecursiveWork:
    lineage_id: str
    work_fingerprint: str
    work: WorkUnit
    budget_scope_id: str
    parent_fingerprint: str | None
    ancestry_fingerprints: frozenset[str]
    effective_capabilities: tuple[str, ...]
    generation: int


class SqliteRecursiveWorkStore:
    """Durable recursive work lineage with exact immutable-state validation."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript(_SCHEMA)
            _migrate_recursive_capability_schema(self.connection)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def put_initial(
        self,
        *,
        work: WorkUnit,
        lineage_id: str,
        budget_scope_id: str,
        parent_fingerprint: str | None,
        ancestry_fingerprints: Collection[str],
        effective_capabilities: Collection[str],
    ) -> StoredRecursiveWork:
        if parent_fingerprint is not None or work.recursion_depth != 0:
            raise ValueError(
                "child recursive work requires atomic recursive admission"
            )
        if not lineage_id.strip():
            raise ValueError("recursive work lineage id is required")
        if not budget_scope_id.strip():
            raise ValueError("recursive work budget scope id is required")

        fingerprint = work_unit_fingerprint(work)
        ancestry = frozenset(str(item) for item in ancestry_fingerprints)
        if any(not item for item in ancestry):
            raise ValueError("recursive work ancestry contains an empty fingerprint")
        capabilities = _normalize_capabilities(effective_capabilities)
        if not set(work.required_capabilities).issubset(set(capabilities)):
            raise ValueError(
                "work capability requirement exceeds durable capability ceiling"
            )

        parent: StoredRecursiveWork | None = None
        if parent_fingerprint is None:
            if work.recursion_depth != 0:
                raise ValueError("non-root recursive work requires a parent fingerprint")
            if budget_scope_id != "root":
                raise ValueError("root recursive work requires the root budget scope")
            expected_ancestry = frozenset({fingerprint})
        else:
            if work.recursion_depth <= 0:
                raise ValueError("root recursive work cannot declare a parent fingerprint")
            if parent_fingerprint == fingerprint:
                raise ValueError("recursive work cannot parent itself")
            parent = self.get(lineage_id, parent_fingerprint)
            if parent is None:
                raise ValueError("recursive work parent is not durable")
            if work.recursion_depth != parent.work.recursion_depth + 1:
                raise ValueError("recursive work depth does not follow durable parent")
            expected_scope = f"work:{fingerprint}"
            if budget_scope_id != expected_scope:
                raise ValueError("child recursive work budget scope does not match work identity")
            expected_ancestry = parent.ancestry_fingerprints | {fingerprint}

        if ancestry != expected_ancestry:
            raise ValueError("recursive work ancestry does not extend durable parent exactly")

        work_json = _canonical_json(_work_payload(work))
        ancestry_json = _canonical_json(sorted(ancestry))
        effective_capabilities_json = _canonical_json(list(capabilities))
        immutable_sha256 = _immutable_digest(
            work_json=work_json,
            lineage_id=lineage_id,
            budget_scope_id=budget_scope_id,
            parent_fingerprint=parent_fingerprint,
            ancestry_json=ancestry_json,
            effective_capabilities_json=effective_capabilities_json,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO recursive_work_state (
                    lineage_id, work_fingerprint, work_json, budget_scope_id,
                    parent_fingerprint, ancestry_json, effective_capabilities_json,
                    immutable_sha256, status, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    lineage_id,
                    fingerprint,
                    work_json,
                    budget_scope_id,
                    parent_fingerprint,
                    ancestry_json,
                    effective_capabilities_json,
                    immutable_sha256,
                    work.status.value,
                ),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("recursive work state already exists") from exc
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        stored = self.get(lineage_id, fingerprint)
        assert stored is not None
        return stored

    def get(
        self,
        lineage_id: str,
        work_fingerprint_value: str,
    ) -> StoredRecursiveWork | None:
        row = self.connection.execute(
            """
            SELECT work_json, budget_scope_id, parent_fingerprint,
                   ancestry_json, effective_capabilities_json,
                   immutable_sha256, status, generation
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (lineage_id, work_fingerprint_value),
        ).fetchone()
        if row is None:
            return None

        (
            work_json,
            budget_scope_id,
            parent_fingerprint,
            ancestry_json,
            effective_capabilities_json,
            immutable_sha256,
            raw_status,
            generation,
        ) = row

        expected_digest = _immutable_digest(
            work_json=str(work_json),
            lineage_id=lineage_id,
            budget_scope_id=str(budget_scope_id),
            parent_fingerprint=(
                str(parent_fingerprint) if parent_fingerprint is not None else None
            ),
            ancestry_json=str(ancestry_json),
            effective_capabilities_json=str(effective_capabilities_json),
        )
        if not hmac.compare_digest(str(immutable_sha256), expected_digest):
            raise ValueError("recursive work state digest mismatch")

        try:
            work_payload = json.loads(str(work_json))
            ancestry_payload = json.loads(str(ancestry_json))
            capability_payload = json.loads(str(effective_capabilities_json))
            status = WorkUnitStatus(str(raw_status))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("recursive work state is structurally invalid") from exc

        if not isinstance(work_payload, Mapping):
            raise ValueError("recursive work payload is structurally invalid")
        if not isinstance(ancestry_payload, list) or any(
            not isinstance(item, str) or not item for item in ancestry_payload
        ):
            raise ValueError("recursive work ancestry is structurally invalid")
        if not isinstance(capability_payload, list):
            raise ValueError("recursive work capability ceiling is structurally invalid")
        capabilities = _normalize_capabilities(capability_payload)
        if capability_payload != list(capabilities):
            raise ValueError("recursive work capability ceiling is not canonical")

        work = _work_from_payload(work_payload, status=status)
        if not set(work.required_capabilities).issubset(set(capabilities)):
            raise ValueError(
                "recursive work requirement exceeds durable capability ceiling"
            )
        observed_fingerprint = work_unit_fingerprint(work)
        if observed_fingerprint != work_fingerprint_value:
            raise ValueError("recursive work semantic identity mismatch")

        ancestry = frozenset(ancestry_payload)
        if observed_fingerprint not in ancestry:
            raise ValueError("recursive work ancestry omits current work")

        if parent_fingerprint is None:
            if work.recursion_depth != 0 or budget_scope_id != "root":
                raise ValueError("recursive root state is inconsistent")
            if ancestry != frozenset({observed_fingerprint}):
                raise ValueError("recursive root ancestry is inconsistent")
        else:
            parent_fingerprint = str(parent_fingerprint)
            if parent_fingerprint == observed_fingerprint:
                raise ValueError("recursive work cannot parent itself")
            expected_scope = f"work:{observed_fingerprint}"
            if budget_scope_id != expected_scope:
                raise ValueError("recursive child budget scope is inconsistent")
            if parent_fingerprint not in ancestry:
                raise ValueError("recursive child ancestry omits parent")

        return StoredRecursiveWork(
            lineage_id=lineage_id,
            work_fingerprint=work_fingerprint_value,
            work=work,
            budget_scope_id=str(budget_scope_id),
            parent_fingerprint=parent_fingerprint,
            ancestry_fingerprints=ancestry,
            effective_capabilities=capabilities,
            generation=int(generation),
        )

    def compare_and_swap_status(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        expected_generation: int,
        status: WorkUnitStatus,
        lease: Lease | None = None,
        now: float | None = None,
    ) -> StoredRecursiveWork:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if row is None:
                raise ValueError("recursive work state not found")

            current_status = WorkUnitStatus(str(row[0]))
            current_generation = int(row[1])
            if current_generation != expected_generation:
                raise ValueError("recursive work generation mismatch")

            if current_status in _TERMINAL_STATUSES:
                if status is not current_status:
                    raise ValueError("terminal recursive work state cannot transition")
                self.connection.commit()
                stored = self.get(lineage_id, work_fingerprint_value)
                if stored is None:
                    raise ValueError("recursive work state disappeared after no-op")
                return stored

            if status is not current_status:
                allowed = _ALLOWED_STATUS_TRANSITIONS.get(
                    current_status,
                    frozenset(),
                )
                if status not in allowed:
                    raise ValueError(
                        "recursive work lifecycle transition is invalid: "
                        f"{current_status.value} -> {status.value}"
                    )

            if status in _LEASE_BOUND_NONTERMINAL_STATUSES:
                _validate_active_lease_state(
                    self.connection,
                    work_fingerprint_value=work_fingerprint_value,
                    lease=lease,
                    now=now,
                )

            if status in _TERMINAL_STATUSES:
                raise ValueError(
                    "terminal recursive work status requires atomic finalization"
                )

            if status is current_status:
                self.connection.commit()
                stored = self.get(lineage_id, work_fingerprint_value)
                if stored is None:
                    raise ValueError("recursive work state disappeared after no-op")
                return stored

            cursor = self.connection.execute(
                """
                UPDATE recursive_work_state
                SET status = ?, generation = generation + 1
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND generation = ?
                """,
                (
                    status.value,
                    lineage_id,
                    work_fingerprint_value,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("recursive work generation changed during status update")
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        stored = self.get(lineage_id, work_fingerprint_value)
        if stored is None:
            raise ValueError("recursive work state disappeared after update")
        return stored


    def finalize_terminal_status(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        expected_generation: int,
        status: WorkUnitStatus,
        lease: Lease,
        now: float,
    ) -> StoredRecursiveWork:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("atomic finalization requires a terminal status")

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if row is None:
                raise ValueError("recursive work state not found")

            current_status = WorkUnitStatus(str(row[0]))
            current_generation = int(row[1])
            if current_generation != expected_generation:
                raise ValueError("recursive work generation mismatch")

            if current_status in _TERMINAL_STATUSES:
                if status is not current_status:
                    raise ValueError("terminal recursive work state cannot transition")
                self.connection.commit()
                stored = self.get(lineage_id, work_fingerprint_value)
                if stored is None:
                    raise ValueError("recursive work state disappeared after no-op")
                return stored

            allowed = _ALLOWED_STATUS_TRANSITIONS.get(
                current_status,
                frozenset(),
            )
            if status not in allowed:
                raise ValueError(
                    "recursive work lifecycle transition is invalid: "
                    f"{current_status.value} -> {status.value}"
                )

            _validate_active_lease_state(
                self.connection,
                work_fingerprint_value=work_fingerprint_value,
                lease=lease,
                now=now,
            )

            if status is WorkUnitStatus.COMPLETE:
                lease_update = self.connection.execute(
                    """
                    UPDATE leases
                    SET completed = 1
                    WHERE work_fingerprint = ?
                      AND holder = ?
                      AND fencing_token = ?
                      AND completed = 0
                    """,
                    (
                        work_fingerprint_value,
                        lease.holder,
                        lease.fencing_token,
                    ),
                )
            else:
                lease_update = self.connection.execute(
                    """
                    UPDATE leases
                    SET holder = NULL, expires_at = 0, completed = 0
                    WHERE work_fingerprint = ?
                      AND holder = ?
                      AND fencing_token = ?
                      AND completed = 0
                    """,
                    (
                        work_fingerprint_value,
                        lease.holder,
                        lease.fencing_token,
                    ),
                )
            if lease_update.rowcount != 1:
                raise ValueError(
                    "recursive work lease changed during atomic finalization"
                )

            work_update = self.connection.execute(
                """
                UPDATE recursive_work_state
                SET status = ?, generation = generation + 1
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND generation = ?
                """,
                (
                    status.value,
                    lineage_id,
                    work_fingerprint_value,
                    expected_generation,
                ),
            )
            if work_update.rowcount != 1:
                raise ValueError(
                    "recursive work generation changed during atomic finalization"
                )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        stored = self.get(lineage_id, work_fingerprint_value)
        if stored is None:
            raise ValueError("recursive work state disappeared after finalization")
        return stored


def _validate_active_lease_state(
    connection: sqlite3.Connection,
    *,
    work_fingerprint_value: str,
    lease: Lease | None,
    now: float | None,
) -> None:
    if lease is None:
        raise ValueError("active recursive work status requires a lease fence")
    if now is None:
        raise ValueError("active recursive work status requires current time")
    if lease.work_fingerprint != work_fingerprint_value:
        raise ValueError("active recursive work lease subject mismatch")

    try:
        row = connection.execute(
            """
            SELECT holder, fencing_token, expires_at, completed
            FROM leases
            WHERE work_fingerprint = ?
            """,
            (work_fingerprint_value,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError(
            "active recursive work lease state is unavailable"
        ) from exc
    if row is None:
        raise ValueError("active recursive work lease state not found")

    holder, fencing_token, expires_at, completed = row
    if holder != lease.holder or int(fencing_token) != lease.fencing_token:
        raise ValueError("active recursive work fencing token is stale")
    if bool(completed):
        raise ValueError("active recursive work lease is already completed")
    if now >= float(expires_at):
        raise ValueError("active recursive work lease is expired")


def _validate_terminal_lease_state(
    connection: sqlite3.Connection,
    *,
    work_fingerprint_value: str,
    lease: Lease | None,
    status: WorkUnitStatus,
) -> None:
    if lease is None:
        raise ValueError("terminal recursive work status requires a lease fence")
    if lease.work_fingerprint != work_fingerprint_value:
        raise ValueError("terminal recursive work lease subject mismatch")

    try:
        row = connection.execute(
            """
            SELECT holder, fencing_token, expires_at, completed
            FROM leases
            WHERE work_fingerprint = ?
            """,
            (work_fingerprint_value,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError(
            "terminal recursive work lease state is unavailable"
        ) from exc
    if row is None:
        raise ValueError("terminal recursive work lease state not found")

    holder, fencing_token, expires_at, completed = row
    if int(fencing_token) != lease.fencing_token:
        raise ValueError("terminal recursive work fencing token is stale")

    if status is WorkUnitStatus.COMPLETE:
        if holder != lease.holder or not bool(completed):
            raise ValueError(
                "terminal recursive work completion is not lease-authorized"
            )
        return

    if status in {
        WorkUnitStatus.FAILED_DETERMINISTIC,
        WorkUnitStatus.SUPERSEDED,
    }:
        if holder is not None or bool(completed) or float(expires_at) != 0.0:
            raise ValueError(
                "terminal recursive work failure/supersession lease is not released"
            )
        return

    raise ValueError("unsupported terminal recursive work status")


def _subject_payload(subject: ExactSubject) -> dict[str, str | None]:
    return {
        "repository": subject.repository,
        "ref": subject.ref,
        "commit": subject.commit,
        "path": subject.path,
        "digest": subject.digest,
    }


def _work_payload(work: WorkUnit) -> dict[str, Any]:
    return {
        "id": work.id,
        "root_frontier_id": work.root_frontier_id,
        "parent_work_id": work.parent_work_id,
        "inputs": [_subject_payload(subject) for subject in work.inputs],
        "operation": work.operation,
        "required_capabilities": list(work.required_capabilities),
        "collision_keys": list(work.collision_keys),
        "recursion_depth": work.recursion_depth,
        "budget_allocation": dict(work.budget_allocation),
        "expected_outputs": list(work.expected_outputs),
        "completion_criteria": list(work.completion_criteria),
        "payload": work.payload,
    }


def _work_from_payload(
    payload: Mapping[str, Any],
    *,
    status: WorkUnitStatus,
) -> WorkUnit:
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("recursive work inputs are structurally invalid")

    try:
        subjects = tuple(
            ExactSubject.from_mapping(item)
            for item in inputs
            if isinstance(item, Mapping)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recursive work subjects are structurally invalid") from exc
    if len(subjects) != len(inputs):
        raise ValueError("recursive work subjects are structurally invalid")

    raw_budget = payload.get("budget_allocation")
    raw_payload = payload.get("payload")
    if not isinstance(raw_budget, Mapping) or not isinstance(raw_payload, Mapping):
        raise ValueError("recursive work mappings are structurally invalid")

    try:
        return WorkUnit(
            id=str(payload["id"]),
            root_frontier_id=str(payload["root_frontier_id"]),
            parent_work_id=(
                str(payload["parent_work_id"])
                if payload.get("parent_work_id") is not None
                else None
            ),
            inputs=subjects,
            operation=str(payload["operation"]),
            required_capabilities=tuple(str(item) for item in payload["required_capabilities"]),
            collision_keys=tuple(str(item) for item in payload["collision_keys"]),
            recursion_depth=int(payload["recursion_depth"]),
            budget_allocation={str(k): int(v) for k, v in raw_budget.items()},
            expected_outputs=tuple(str(item) for item in payload["expected_outputs"]),
            completion_criteria=tuple(str(item) for item in payload["completion_criteria"]),
            status=status,
            payload=dict(raw_payload),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("recursive work payload is structurally invalid") from exc


def _normalize_capabilities(values: Collection[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(str(item) for item in values)))
    if any(not item for item in normalized):
        raise ValueError("durable capability ceiling contains an empty capability")
    return normalized


def _migrate_recursive_capability_schema(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(recursive_work_state)")
    }
    if "effective_capabilities_json" in columns:
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            ALTER TABLE recursive_work_state
            ADD COLUMN effective_capabilities_json TEXT NOT NULL DEFAULT '[]'
            """
        )
        rows = connection.execute(
            """
            SELECT lineage_id, work_fingerprint, work_json, budget_scope_id,
                   parent_fingerprint, ancestry_json, immutable_sha256
            FROM recursive_work_state
            """
        ).fetchall()
        for (
            lineage_id,
            work_fingerprint_value,
            work_json,
            budget_scope_id,
            parent_fingerprint,
            ancestry_json,
            legacy_immutable_sha256,
        ) in rows:
            expected_legacy_digest = _legacy_immutable_digest(
                work_json=str(work_json),
                lineage_id=str(lineage_id),
                budget_scope_id=str(budget_scope_id),
                parent_fingerprint=(
                    str(parent_fingerprint)
                    if parent_fingerprint is not None
                    else None
                ),
                ancestry_json=str(ancestry_json),
            )
            if not hmac.compare_digest(
                str(legacy_immutable_sha256),
                expected_legacy_digest,
            ):
                raise ValueError(
                    "legacy recursive work state digest mismatch during migration"
                )

            try:
                work_payload = json.loads(str(work_json))
                raw_capabilities = work_payload["required_capabilities"]
                if not isinstance(raw_capabilities, list):
                    raise ValueError
                capabilities = _normalize_capabilities(raw_capabilities)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "legacy recursive work capability migration failed"
                ) from exc
            effective_capabilities_json = _canonical_json(list(capabilities))
            immutable_sha256 = _immutable_digest(
                work_json=str(work_json),
                lineage_id=str(lineage_id),
                budget_scope_id=str(budget_scope_id),
                parent_fingerprint=(
                    str(parent_fingerprint)
                    if parent_fingerprint is not None
                    else None
                ),
                ancestry_json=str(ancestry_json),
                effective_capabilities_json=effective_capabilities_json,
            )
            connection.execute(
                """
                UPDATE recursive_work_state
                SET effective_capabilities_json = ?, immutable_sha256 = ?
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (
                    effective_capabilities_json,
                    immutable_sha256,
                    lineage_id,
                    work_fingerprint_value,
                ),
            )
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _legacy_immutable_digest(
    *,
    work_json: str,
    lineage_id: str,
    budget_scope_id: str,
    parent_fingerprint: str | None,
    ancestry_json: str,
) -> str:
    payload = _canonical_json(
        {
            "work_json": work_json,
            "lineage_id": lineage_id,
            "budget_scope_id": budget_scope_id,
            "parent_fingerprint": parent_fingerprint,
            "ancestry_json": ancestry_json,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _immutable_digest(
    *,
    work_json: str,
    lineage_id: str,
    budget_scope_id: str,
    parent_fingerprint: str | None,
    ancestry_json: str,
    effective_capabilities_json: str,
) -> str:
    payload = _canonical_json(
        {
            "work_json": work_json,
            "lineage_id": lineage_id,
            "budget_scope_id": budget_scope_id,
            "parent_fingerprint": parent_fingerprint,
            "ancestry_json": ancestry_json,
            "effective_capabilities_json": effective_capabilities_json,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
