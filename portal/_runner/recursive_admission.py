from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
from pathlib import Path
import sqlite3

from .budgets import BudgetEnvelope
from .decompose import ChildAdmission, admit_child_work
from .persistent_state import _SCHEMA as _PERSISTENT_SCHEMA
from .persistent_state import _migrate_budget_scope_schema
from .recursive_state import (
    _SCHEMA as _RECURSIVE_SCHEMA,
    _TERMINAL_STATUSES,
    _canonical_json,
    _immutable_digest,
    _migrate_recursive_capability_schema,
    _normalize_capabilities,
    _work_from_payload,
    _work_payload,
)
from .work_units import work_unit_fingerprint


@dataclass(frozen=True)
class DurableChildAdmission:
    lineage_id: str
    parent_work_fingerprint: str
    child_work_fingerprint: str
    parent_budget_generation: int
    child_budget_generation: int
    child_work_generation: int


class SqliteRecursiveAdmissionStore:
    """Atomically commits one recursive child admission and its budget transfer."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript(_PERSISTENT_SCHEMA)
            _migrate_budget_scope_schema(self.connection)
            self.connection.executescript(_RECURSIVE_SCHEMA)
            _migrate_recursive_capability_schema(self.connection)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def commit_child(
        self,
        *,
        parent_work_fingerprint: str,
        parent_budget_before: BudgetEnvelope,
        expected_parent_budget_generation: int,
        target_capabilities,
        admission: ChildAdmission,
    ) -> DurableChildAdmission:
        child_work = admission.work
        child_budget = admission.child_budget
        parent_budget_after = admission.parent_budget
        child_fingerprint = work_unit_fingerprint(child_work)

        _validate_budget_transfer(
            parent_budget_before=parent_budget_before,
            parent_budget_after=parent_budget_after,
            child_budget=child_budget,
            child_fingerprint=child_fingerprint,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")

            parent_work_row = self.connection.execute(
                """
                SELECT work_json, budget_scope_id, parent_fingerprint,
                       ancestry_json, effective_capabilities_json,
                       immutable_sha256, status
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (
                    parent_budget_before.lineage_id,
                    parent_work_fingerprint,
                ),
            ).fetchone()
            if parent_work_row is None:
                raise ValueError("durable parent work state not found")

            (
                parent_work_json,
                parent_scope_id,
                durable_parent_fingerprint,
                parent_ancestry_json,
                parent_capabilities_json,
                parent_immutable_sha256,
                parent_status_raw,
            ) = parent_work_row

            expected_parent_digest = _immutable_digest(
                work_json=str(parent_work_json),
                lineage_id=parent_budget_before.lineage_id,
                budget_scope_id=str(parent_scope_id),
                parent_fingerprint=(
                    str(durable_parent_fingerprint)
                    if durable_parent_fingerprint is not None
                    else None
                ),
                ancestry_json=str(parent_ancestry_json),
                effective_capabilities_json=str(parent_capabilities_json),
            )
            if not hmac.compare_digest(
                str(parent_immutable_sha256),
                expected_parent_digest,
            ):
                raise ValueError("durable parent work state digest mismatch")

            from .work_units import WorkUnitStatus

            parent_status = WorkUnitStatus(str(parent_status_raw))
            if parent_status in _TERMINAL_STATUSES:
                raise ValueError("terminal durable parent cannot admit child")
            if str(parent_scope_id) != parent_budget_before.scope_id:
                raise ValueError("durable parent work/budget scope mismatch")

            try:
                parent_ancestry_payload = json.loads(str(parent_ancestry_json))
                parent_capability_payload = json.loads(str(parent_capabilities_json))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "durable parent ancestry/capabilities are structurally invalid"
                ) from exc
            if not isinstance(parent_ancestry_payload, list) or any(
                not isinstance(item, str) or not item
                for item in parent_ancestry_payload
            ):
                raise ValueError("durable parent ancestry is structurally invalid")
            if not isinstance(parent_capability_payload, list):
                raise ValueError(
                    "durable parent capability ceiling is structurally invalid"
                )
            parent_capabilities = _normalize_capabilities(parent_capability_payload)
            if parent_capability_payload != list(parent_capabilities):
                raise ValueError("durable parent capability ceiling is not canonical")
            parent_ancestry = frozenset(parent_ancestry_payload)

            try:
                parent_work_payload = json.loads(str(parent_work_json))
                parent_work = _work_from_payload(
                    parent_work_payload,
                    status=parent_status,
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(
                    "durable parent work payload is structurally invalid"
                ) from exc
            if work_unit_fingerprint(parent_work) != parent_work_fingerprint:
                raise ValueError("durable parent semantic identity mismatch")
            if child_work.status is not WorkUnitStatus.PENDING:
                raise ValueError("recursive child work must begin pending")

            budget_row = self.connection.execute(
                """
                SELECT max_depth, depth, remaining_children, remaining_active,
                       remaining_retries, remaining_backend_jobs, generation
                FROM lineage_budgets
                WHERE lineage_id = ? AND scope_id = ?
                """,
                (
                    parent_budget_before.lineage_id,
                    parent_budget_before.scope_id,
                ),
            ).fetchone()
            if budget_row is None:
                raise ValueError("durable parent budget not found")

            (
                max_depth,
                depth,
                remaining_children,
                remaining_active,
                remaining_retries,
                remaining_backend_jobs,
                parent_generation,
            ) = budget_row
            observed_parent = BudgetEnvelope(
                lineage_id=parent_budget_before.lineage_id,
                max_depth=int(max_depth),
                depth=int(depth),
                remaining_children=int(remaining_children),
                remaining_active=int(remaining_active),
                remaining_retries=int(remaining_retries),
                remaining_backend_jobs=int(remaining_backend_jobs),
                scope_id=parent_budget_before.scope_id,
            )
            if observed_parent != parent_budget_before:
                raise ValueError("durable parent budget does not match admission subject")
            if int(parent_generation) != expected_parent_budget_generation:
                raise ValueError("parent budget generation mismatch")

            recomputed = admit_child_work(
                parent=parent_work,
                child=child_work,
                parent_budget=observed_parent,
                parent_capabilities=parent_capabilities,
                target_capabilities=target_capabilities,
                ancestry_fingerprints=parent_ancestry,
                child_children=child_budget.remaining_children,
                child_active=child_budget.remaining_active,
                child_retries=child_budget.remaining_retries,
                child_backend_jobs=child_budget.remaining_backend_jobs,
            )
            if recomputed != admission:
                raise ValueError(
                    "child admission does not match durable parent recomputation"
                )

            updated = self.connection.execute(
                """
                UPDATE lineage_budgets
                SET max_depth = ?, depth = ?, remaining_children = ?,
                    remaining_active = ?, remaining_retries = ?,
                    remaining_backend_jobs = ?, generation = generation + 1
                WHERE lineage_id = ?
                  AND scope_id = ?
                  AND generation = ?
                """,
                (
                    parent_budget_after.max_depth,
                    parent_budget_after.depth,
                    parent_budget_after.remaining_children,
                    parent_budget_after.remaining_active,
                    parent_budget_after.remaining_retries,
                    parent_budget_after.remaining_backend_jobs,
                    parent_budget_before.lineage_id,
                    parent_budget_before.scope_id,
                    expected_parent_budget_generation,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("parent budget generation changed during admission")

            self.connection.execute(
                """
                INSERT INTO lineage_budgets (
                    lineage_id, scope_id, max_depth, depth,
                    remaining_children, remaining_active, remaining_retries,
                    remaining_backend_jobs, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    child_budget.lineage_id,
                    child_budget.scope_id,
                    child_budget.max_depth,
                    child_budget.depth,
                    child_budget.remaining_children,
                    child_budget.remaining_active,
                    child_budget.remaining_retries,
                    child_budget.remaining_backend_jobs,
                ),
            )

            child_work_json = _canonical_json(_work_payload(child_work))
            child_ancestry_json = _canonical_json(
                sorted(admission.ancestry_fingerprints)
            )
            child_capabilities_json = _canonical_json(
                list(recomputed.effective_capabilities)
            )
            child_immutable_sha256 = _immutable_digest(
                work_json=child_work_json,
                lineage_id=child_budget.lineage_id,
                budget_scope_id=child_budget.scope_id,
                parent_fingerprint=parent_work_fingerprint,
                ancestry_json=child_ancestry_json,
                effective_capabilities_json=child_capabilities_json,
            )
            self.connection.execute(
                """
                INSERT INTO recursive_work_state (
                    lineage_id, work_fingerprint, work_json, budget_scope_id,
                    parent_fingerprint, ancestry_json, effective_capabilities_json,
                    immutable_sha256, status, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    child_budget.lineage_id,
                    child_fingerprint,
                    child_work_json,
                    child_budget.scope_id,
                    parent_work_fingerprint,
                    child_ancestry_json,
                    child_capabilities_json,
                    child_immutable_sha256,
                    child_work.status.value,
                ),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("child admission collides with durable state") from exc
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        return DurableChildAdmission(
            lineage_id=parent_budget_before.lineage_id,
            parent_work_fingerprint=parent_work_fingerprint,
            child_work_fingerprint=child_fingerprint,
            parent_budget_generation=expected_parent_budget_generation + 1,
            child_budget_generation=1,
            child_work_generation=1,
        )


def _validate_budget_transfer(
    *,
    parent_budget_before: BudgetEnvelope,
    parent_budget_after: BudgetEnvelope,
    child_budget: BudgetEnvelope,
    child_fingerprint: str,
) -> None:
    if parent_budget_after.lineage_id != parent_budget_before.lineage_id:
        raise ValueError("parent budget lineage changed during child admission")
    if child_budget.lineage_id != parent_budget_before.lineage_id:
        raise ValueError("child budget lineage does not match parent")
    if parent_budget_after.scope_id != parent_budget_before.scope_id:
        raise ValueError("parent budget scope changed during child admission")
    if parent_budget_after.max_depth != parent_budget_before.max_depth:
        raise ValueError("parent max depth changed during child admission")
    if parent_budget_after.depth != parent_budget_before.depth:
        raise ValueError("parent depth changed during child admission")
    if child_budget.max_depth != parent_budget_before.max_depth:
        raise ValueError("child max depth does not match parent")
    if child_budget.depth != parent_budget_before.depth + 1:
        raise ValueError("child budget depth does not follow parent")
    if child_budget.scope_id != f"work:{child_fingerprint}":
        raise ValueError("child budget scope does not match semantic work identity")

    dimensions = (
        ("children", "remaining_children"),
        ("active", "remaining_active"),
        ("retries", "remaining_retries"),
        ("backend jobs", "remaining_backend_jobs"),
    )
    for label, field in dimensions:
        before = getattr(parent_budget_before, field)
        after = getattr(parent_budget_after, field)
        child = getattr(child_budget, field)
        if after > before:
            raise ValueError(f"parent {label} budget increased during child admission")
        if before - after != child:
            raise ValueError(f"child {label} budget does not match parent transfer")
