from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from .backends import BackendResult, ExecutionBackend
from .budgets import BudgetEnvelope
from .dispatch import DispatchAttempt
from .leases import Lease, LeaseStore
from .models import Frontier
from .persistent_state import _SCHEMA as _PERSISTENT_SCHEMA
from .persistent_state import _migrate_budget_scope_schema
from .recursive_state import (
    _ALLOWED_STATUS_TRANSITIONS,
    _TERMINAL_STATUSES,
    _SCHEMA as _RECURSIVE_SCHEMA,
    _canonical_json as _recursive_canonical_json,
    _immutable_digest,
    _migrate_recursive_capability_schema,
    _normalize_capabilities,
    _validate_active_lease_state,
    _work_from_payload,
    _work_payload,
)
from .verify import EvidenceVerifier, SubjectReader, VerificationOutcome, verify_attempt
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint



_RECONCILIATION_OUTCOMES = frozenset(
    {"INDETERMINATE", "NO_EFFECT_CONFIRMED", "EFFECT_CONFIRMED"}
)

_EXECUTION_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_attempts (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    holder TEXT NOT NULL,
    admitted_at REAL NOT NULL,
    budget_generation INTEGER NOT NULL,
    work_generation INTEGER NOT NULL,
    attempt_sha256 TEXT NOT NULL,
    result_json TEXT,
    result_sha256 TEXT,
    result_recorded_at REAL,
    PRIMARY KEY (lineage_id, work_fingerprint, fencing_token)
);

CREATE TABLE IF NOT EXISTS execution_verifications (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    verified_at REAL NOT NULL,
    verification_sha256 TEXT NOT NULL,
    PRIMARY KEY (lineage_id, work_fingerprint, fencing_token, sequence),
    FOREIGN KEY (lineage_id, work_fingerprint, fencing_token)
        REFERENCES execution_attempts (lineage_id, work_fingerprint, fencing_token)
);

CREATE TABLE IF NOT EXISTS execution_reconciliations (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    reconciler TEXT NOT NULL,
    observed_at REAL NOT NULL,
    reconciliation_sha256 TEXT NOT NULL,
    PRIMARY KEY (lineage_id, work_fingerprint, fencing_token, sequence),
    FOREIGN KEY (lineage_id, work_fingerprint, fencing_token)
        REFERENCES execution_attempts (lineage_id, work_fingerprint, fencing_token)
);
"""


@dataclass(frozen=True)
class DurableExecutionVerification:
    sequence: int
    status: WorkUnitStatus
    reason: str
    verified_at: float
    sha256: str


@dataclass(frozen=True)
class DurableExecutionReconciliation:
    sequence: int
    outcome: str
    reason: str
    evidence: tuple[str, ...]
    reconciler: str
    observed_at: float
    sha256: str


@dataclass(frozen=True)
class DurableExecutionJournalEntry:
    lineage_id: str
    work_fingerprint: str
    fencing_token: int
    holder: str
    admitted_at: float
    budget_generation: int
    work_generation: int
    phase: str
    result_sha256: str | None
    last_verification_status: WorkUnitStatus | None
    last_verification_reason: str | None
    last_reconciliation_outcome: str | None = None
    last_reconciliation_reason: str | None = None


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attempt_digest(
    *,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    holder: str,
    admitted_at: float,
    budget_generation: int,
    work_generation: int,
) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "admitted_at": admitted_at,
                "budget_generation": budget_generation,
                "fencing_token": fencing_token,
                "holder": holder,
                "lineage_id": lineage_id,
                "work_fingerprint": work_fingerprint_value,
                "work_generation": work_generation,
            }
        )
    )


def _result_payload(result: BackendResult) -> str:
    return _canonical_json(
        {
            "classification": result.classification,
            "evidence": list(result.evidence),
            "outputs": list(result.outputs),
            "succeeded": result.succeeded,
            "work_fingerprint": result.work_fingerprint,
        }
    )


def _verification_digest(
    *,
    status: WorkUnitStatus,
    reason: str,
    verified_at: float,
) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "reason": reason,
                "status": status.value,
                "verified_at": verified_at,
            }
        )
    )


def _reconciliation_digest(
    *,
    outcome: str,
    reason: str,
    evidence: tuple[str, ...],
    reconciler: str,
    observed_at: float,
) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "evidence": list(evidence),
                "observed_at": observed_at,
                "outcome": outcome,
                "reason": reason,
                "reconciler": reconciler,
            }
        )
    )


@dataclass(frozen=True)
class DurableDispatchAdmission:
    work: WorkUnit
    lease: Lease
    budget_after: BudgetEnvelope
    budget_generation: int
    work_generation: int
    retry_consumed: int


class SqliteDispatchAdmissionStore:
    """Atomically reserve execution budget, claim the lease, and mark work CLAIMED."""

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
            self.connection.executescript(_EXECUTION_JOURNAL_SCHEMA)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def initialize_root(
        self,
        *,
        budget: BudgetEnvelope,
        work: WorkUnit,
        effective_capabilities,
    ) -> tuple[int, int]:
        """Atomically seed the root budget and root recursive work record."""
        if budget.scope_id != "root":
            raise ValueError("root initialization requires the root budget scope")
        if work.recursion_depth != 0 or work.parent_work_id is not None:
            raise ValueError("root initialization requires root work")
        if work.status is not WorkUnitStatus.PENDING:
            raise ValueError("root initialization requires PENDING work")
        if budget.depth != work.recursion_depth:
            raise ValueError("root budget depth does not match root work")
        if not budget.lineage_id.strip():
            raise ValueError("root initialization requires a lineage id")

        fingerprint = work_unit_fingerprint(work)
        capabilities = _normalize_capabilities(effective_capabilities)
        if not set(work.required_capabilities).issubset(set(capabilities)):
            raise ValueError(
                "root work capability requirement exceeds capability ceiling"
            )

        work_json = _recursive_canonical_json(_work_payload(work))
        ancestry_json = _recursive_canonical_json([fingerprint])
        capabilities_json = _recursive_canonical_json(list(capabilities))
        immutable_sha256 = _immutable_digest(
            work_json=work_json,
            lineage_id=budget.lineage_id,
            budget_scope_id=budget.scope_id,
            parent_fingerprint=None,
            ancestry_json=ancestry_json,
            effective_capabilities_json=capabilities_json,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO lineage_budgets (
                    lineage_id, scope_id, max_depth, depth, remaining_children,
                    remaining_active, remaining_retries,
                    remaining_backend_jobs, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    budget.lineage_id,
                    budget.scope_id,
                    budget.max_depth,
                    budget.depth,
                    budget.remaining_children,
                    budget.remaining_active,
                    budget.remaining_retries,
                    budget.remaining_backend_jobs,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO recursive_work_state (
                    lineage_id, work_fingerprint, work_json, budget_scope_id,
                    parent_fingerprint, ancestry_json, effective_capabilities_json,
                    immutable_sha256, status, generation
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 1)
                """,
                (
                    budget.lineage_id,
                    fingerprint,
                    work_json,
                    budget.scope_id,
                    ancestry_json,
                    capabilities_json,
                    immutable_sha256,
                    WorkUnitStatus.PENDING.value,
                ),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("root execution state already exists") from exc
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        return 1, 1

    def admit(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        budget_scope_id: str,
        expected_budget_generation: int,
        expected_work_generation: int,
        holder: str,
        now: float,
        ttl: float,
    ) -> DurableDispatchAdmission:
        if not lineage_id.strip():
            raise ValueError("dispatch lineage id is required")
        if not budget_scope_id.strip():
            raise ValueError("dispatch budget scope id is required")
        if not holder.strip():
            raise ValueError("dispatch lease holder is required")
        if ttl <= 0:
            raise ValueError("dispatch lease ttl must be positive")

        try:
            self.connection.execute("BEGIN IMMEDIATE")

            work_row = self.connection.execute(
                """
                SELECT work_json, budget_scope_id, parent_fingerprint,
                       ancestry_json, effective_capabilities_json,
                       immutable_sha256, status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if work_row is None:
                raise ValueError("durable dispatch work state not found")

            (
                work_json,
                stored_scope_id,
                parent_fingerprint,
                ancestry_json,
                capabilities_json,
                immutable_sha256,
                raw_status,
                work_generation,
            ) = work_row
            if str(stored_scope_id) != budget_scope_id:
                raise ValueError("dispatch work/budget scope mismatch")
            if int(work_generation) != expected_work_generation:
                raise ValueError("dispatch work generation mismatch")

            expected_digest = _immutable_digest(
                work_json=str(work_json),
                lineage_id=lineage_id,
                budget_scope_id=budget_scope_id,
                parent_fingerprint=(
                    str(parent_fingerprint)
                    if parent_fingerprint is not None
                    else None
                ),
                ancestry_json=str(ancestry_json),
                effective_capabilities_json=str(capabilities_json),
            )
            if not hmac.compare_digest(str(immutable_sha256), expected_digest):
                raise ValueError("durable dispatch work state digest mismatch")

            try:
                work_payload = json.loads(str(work_json))
                capability_payload = json.loads(str(capabilities_json))
                current_status = WorkUnitStatus(str(raw_status))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("durable dispatch work state is structurally invalid") from exc
            if not isinstance(work_payload, dict):
                raise ValueError("durable dispatch work payload is structurally invalid")
            if not isinstance(capability_payload, list):
                raise ValueError(
                    "durable dispatch capability ceiling is structurally invalid"
                )
            capabilities = _normalize_capabilities(capability_payload)
            if capability_payload != list(capabilities):
                raise ValueError("durable dispatch capability ceiling is not canonical")

            work = _work_from_payload(work_payload, status=current_status)
            if work_unit_fingerprint(work) != work_fingerprint_value:
                raise ValueError("durable dispatch semantic identity mismatch")
            if not set(work.required_capabilities).issubset(set(capabilities)):
                raise ValueError(
                    "durable dispatch requirement exceeds capability ceiling"
                )

            allowed = _ALLOWED_STATUS_TRANSITIONS.get(current_status, frozenset())
            if WorkUnitStatus.CLAIMED not in allowed:
                raise ValueError(
                    "durable work is not dispatch-admissible from "
                    f"{current_status.value}"
                )

            budget_row = self.connection.execute(
                """
                SELECT max_depth, depth, remaining_children, remaining_active,
                       remaining_retries, remaining_backend_jobs, generation
                FROM lineage_budgets
                WHERE lineage_id = ? AND scope_id = ?
                """,
                (lineage_id, budget_scope_id),
            ).fetchone()
            if budget_row is None:
                raise ValueError("durable dispatch budget not found")

            (
                max_depth,
                depth,
                remaining_children,
                remaining_active,
                remaining_retries,
                remaining_backend_jobs,
                budget_generation,
            ) = budget_row
            if int(budget_generation) != expected_budget_generation:
                raise ValueError("dispatch budget generation mismatch")
            if int(depth) != work.recursion_depth:
                raise ValueError("dispatch budget depth does not match work depth")
            if int(remaining_active) <= 0:
                raise ValueError("active budget exhausted")
            if int(remaining_backend_jobs) <= 0:
                raise ValueError("backend jobs budget exhausted")

            retry_consumed = int(
                current_status
                in {
                    WorkUnitStatus.FAILED_RETRYABLE,
                    WorkUnitStatus.OUTCOME_UNKNOWN,
                }
            )
            if retry_consumed and int(remaining_retries) <= 0:
                raise ValueError("retries budget exhausted")

            lease_row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases
                WHERE work_fingerprint = ?
                """,
                (work_fingerprint_value,),
            ).fetchone()

            if lease_row is None:
                token = 1
                expires_at = now + ttl
                self.connection.execute(
                    """
                    INSERT INTO leases (
                        work_fingerprint, holder, fencing_token, expires_at, completed
                    ) VALUES (?, ?, ?, ?, 0)
                    """,
                    (work_fingerprint_value, holder, token, expires_at),
                )
            else:
                current_holder, current_token, current_expiry, completed = lease_row
                if bool(completed):
                    raise ValueError("completed work cannot be dispatched")
                if current_holder is not None and now < float(current_expiry):
                    raise ValueError("dispatch work already has an active lease")
                token = int(current_token) + 1
                expires_at = now + ttl
                self.connection.execute(
                    """
                    UPDATE leases
                    SET holder = ?, fencing_token = ?, expires_at = ?, completed = 0
                    WHERE work_fingerprint = ?
                    """,
                    (holder, token, expires_at, work_fingerprint_value),
                )

            admitted_budget_generation = expected_budget_generation + 1
            admitted_work_generation = expected_work_generation + 1
            attempt_sha256 = _attempt_digest(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=token,
                holder=holder,
                admitted_at=now,
                budget_generation=admitted_budget_generation,
                work_generation=admitted_work_generation,
            )
            self.connection.execute(
                """
                INSERT INTO execution_attempts (
                    lineage_id, work_fingerprint, fencing_token, holder,
                    admitted_at, budget_generation, work_generation, attempt_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lineage_id,
                    work_fingerprint_value,
                    token,
                    holder,
                    now,
                    admitted_budget_generation,
                    admitted_work_generation,
                    attempt_sha256,
                ),
            )

            budget_after = BudgetEnvelope(
                lineage_id=lineage_id,
                max_depth=int(max_depth),
                depth=int(depth),
                remaining_children=int(remaining_children),
                remaining_active=int(remaining_active) - 1,
                remaining_retries=int(remaining_retries) - retry_consumed,
                remaining_backend_jobs=int(remaining_backend_jobs) - 1,
                scope_id=budget_scope_id,
            )
            budget_update = self.connection.execute(
                """
                UPDATE lineage_budgets
                SET remaining_active = ?,
                    remaining_retries = ?,
                    remaining_backend_jobs = ?,
                    generation = generation + 1
                WHERE lineage_id = ?
                  AND scope_id = ?
                  AND generation = ?
                """,
                (
                    budget_after.remaining_active,
                    budget_after.remaining_retries,
                    budget_after.remaining_backend_jobs,
                    lineage_id,
                    budget_scope_id,
                    expected_budget_generation,
                ),
            )
            if budget_update.rowcount != 1:
                raise ValueError("dispatch budget generation changed during admission")

            work_update = self.connection.execute(
                """
                UPDATE recursive_work_state
                SET status = ?, generation = generation + 1
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND generation = ?
                """,
                (
                    WorkUnitStatus.CLAIMED.value,
                    lineage_id,
                    work_fingerprint_value,
                    expected_work_generation,
                ),
            )
            if work_update.rowcount != 1:
                raise ValueError("dispatch work generation changed during admission")
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("durable dispatch admission collided with state") from exc
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        return DurableDispatchAdmission(
            work=WorkUnit(
                id=work.id,
                root_frontier_id=work.root_frontier_id,
                parent_work_id=work.parent_work_id,
                inputs=work.inputs,
                operation=work.operation,
                required_capabilities=work.required_capabilities,
                collision_keys=work.collision_keys,
                recursion_depth=work.recursion_depth,
                budget_allocation=work.budget_allocation,
                expected_outputs=work.expected_outputs,
                completion_criteria=work.completion_criteria,
                status=WorkUnitStatus.CLAIMED,
                payload=work.payload,
            ),
            lease=Lease(
                work_fingerprint=work_fingerprint_value,
                holder=holder,
                fencing_token=token,
                expires_at=expires_at,
            ),
            budget_after=budget_after,
            budget_generation=expected_budget_generation + 1,
            work_generation=expected_work_generation + 1,
            retry_consumed=retry_consumed,
        )


    def recover_claim_only_root(
        self,
        *,
        budget: BudgetEnvelope,
        work: WorkUnit,
        effective_capabilities,
        holder: str,
        now: float,
        ttl: float,
    ) -> DurableDispatchAdmission:
        """Recover an exact claim-only root after a process interruption.

        PENDING roots resume ordinary admission. Exact active CLAIMED roots are
        idempotently returned to the same holder. Expired CLAIMED roots may be
        re-fenced only when execution is structurally forbidden and no backend
        result/verification/reconciliation was recorded.
        """
        if work.status is not WorkUnitStatus.PENDING:
            raise ValueError("claim-only recovery requires PENDING source work")
        if work.operation != "PORTFOLIO_BOUND_CLAIM":
            raise ValueError("claim-only recovery requires bound-claim work")
        if work.payload.get("execution_authority") is not False:
            raise ValueError("claim-only recovery requires execution_authority=false")
        if work.payload.get("protected_effects_authorized") is not False:
            raise ValueError(
                "claim-only recovery requires protected_effects_authorized=false"
            )
        if not holder.strip():
            raise ValueError("claim-only recovery holder is required")
        if ttl <= 0:
            raise ValueError("claim-only recovery ttl must be positive")
        if budget.scope_id != "root":
            raise ValueError("claim-only recovery requires root budget")

        fingerprint = work_unit_fingerprint(work)
        capabilities = _normalize_capabilities(effective_capabilities)
        expected_work_json = _recursive_canonical_json(_work_payload(work))
        expected_ancestry_json = _recursive_canonical_json([fingerprint])
        expected_capabilities_json = _recursive_canonical_json(list(capabilities))
        expected_immutable = _immutable_digest(
            work_json=expected_work_json,
            lineage_id=budget.lineage_id,
            budget_scope_id=budget.scope_id,
            parent_fingerprint=None,
            ancestry_json=expected_ancestry_json,
            effective_capabilities_json=expected_capabilities_json,
        )

        pending_generations: tuple[int, int] | None = None
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            work_row = self.connection.execute(
                """
                SELECT work_json, budget_scope_id, parent_fingerprint,
                       ancestry_json, effective_capabilities_json,
                       immutable_sha256, status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (budget.lineage_id, fingerprint),
            ).fetchone()
            if work_row is None:
                raise ValueError(
                    "claim-only recovery exact work state is not durable"
                )
            (
                work_json,
                stored_scope_id,
                parent_fingerprint,
                ancestry_json,
                capabilities_json,
                immutable_sha256,
                raw_status,
                raw_work_generation,
            ) = work_row
            if str(stored_scope_id) != budget.scope_id or parent_fingerprint is not None:
                raise ValueError("claim-only recovery work scope mismatch")
            if str(work_json) != expected_work_json:
                raise ValueError("claim-only recovery work payload mismatch")
            if str(ancestry_json) != expected_ancestry_json:
                raise ValueError("claim-only recovery ancestry mismatch")
            if str(capabilities_json) != expected_capabilities_json:
                raise ValueError("claim-only recovery capability ceiling mismatch")
            if not hmac.compare_digest(str(immutable_sha256), expected_immutable):
                raise ValueError("claim-only recovery immutable digest mismatch")

            current_status = WorkUnitStatus(str(raw_status))
            work_generation = int(raw_work_generation)

            budget_row = self.connection.execute(
                """
                SELECT max_depth, depth, remaining_children, remaining_active,
                       remaining_retries, remaining_backend_jobs, generation
                FROM lineage_budgets
                WHERE lineage_id = ? AND scope_id = ?
                """,
                (budget.lineage_id, budget.scope_id),
            ).fetchone()
            if budget_row is None:
                raise ValueError("claim-only recovery budget is not durable")
            (
                max_depth,
                depth,
                remaining_children,
                remaining_active,
                remaining_retries,
                remaining_backend_jobs,
                budget_generation,
            ) = (int(value) for value in budget_row)

            if (
                max_depth != budget.max_depth
                or depth != budget.depth
                or remaining_children != budget.remaining_children
                or remaining_retries != budget.remaining_retries
            ):
                raise ValueError("claim-only recovery budget identity mismatch")

            if current_status is WorkUnitStatus.PENDING:
                if work_generation != 1 or budget_generation != 1:
                    raise ValueError("claim-only pending recovery generation mismatch")
                if (
                    remaining_active != budget.remaining_active
                    or remaining_backend_jobs != budget.remaining_backend_jobs
                ):
                    raise ValueError("claim-only pending recovery budget mismatch")
                if self.connection.execute(
                    """
                    SELECT 1 FROM execution_attempts
                    WHERE lineage_id = ? AND work_fingerprint = ?
                    LIMIT 1
                    """,
                    (budget.lineage_id, fingerprint),
                ).fetchone() is not None:
                    raise ValueError(
                        "claim-only pending recovery unexpectedly has attempt history"
                    )
                if self.connection.execute(
                    "SELECT 1 FROM leases WHERE work_fingerprint = ?",
                    (fingerprint,),
                ).fetchone() is not None:
                    raise ValueError(
                        "claim-only pending recovery unexpectedly has a lease"
                    )
                pending_generations = (budget_generation, work_generation)
                self.connection.commit()
            elif current_status is WorkUnitStatus.CLAIMED:
                expected_active = budget.remaining_active - 1
                expected_backend_jobs = budget.remaining_backend_jobs - 1
                if expected_active < 0 or expected_backend_jobs < 0:
                    raise ValueError(
                        "claim-only recovery source budget cannot support admission"
                    )
                if budget_generation != 2:
                    raise ValueError("claim-only claimed recovery budget generation mismatch")
                if (
                    remaining_active != expected_active
                    or remaining_backend_jobs != expected_backend_jobs
                ):
                    raise ValueError("claim-only claimed recovery budget mismatch")

                lease_row = self.connection.execute(
                    """
                    SELECT holder, fencing_token, expires_at, completed
                    FROM leases WHERE work_fingerprint = ?
                    """,
                    (fingerprint,),
                ).fetchone()
                if lease_row is None:
                    raise ValueError("claim-only claimed recovery lease is missing")
                current_holder, current_token, current_expiry, completed = lease_row
                if bool(completed):
                    raise ValueError("claim-only claimed recovery lease is completed")
                current_token = int(current_token)
                current_expiry = float(current_expiry)

                latest_token_row = self.connection.execute(
                    """
                    SELECT MAX(fencing_token)
                    FROM execution_attempts
                    WHERE lineage_id = ? AND work_fingerprint = ?
                    """,
                    (budget.lineage_id, fingerprint),
                ).fetchone()
                if (
                    latest_token_row is None
                    or latest_token_row[0] is None
                    or int(latest_token_row[0]) != current_token
                ):
                    raise ValueError(
                        "claim-only recovery lease/attempt fencing token mismatch"
                    )

                attempt = self._attempt_row(
                    lineage_id=budget.lineage_id,
                    work_fingerprint_value=fingerprint,
                    fencing_token=current_token,
                )
                if attempt[2] != budget_generation:
                    raise ValueError(
                        "claim-only recovery attempt/budget generation mismatch"
                    )
                if attempt[3] != work_generation:
                    raise ValueError(
                        "claim-only recovery attempt/work generation mismatch"
                    )
                if (
                    current_holder is not None
                    and str(current_holder) != attempt[0]
                ):
                    raise ValueError(
                        "claim-only recovery lease/attempt holder mismatch"
                    )
                if attempt[5] is not None or attempt[6] is not None:
                    raise ValueError(
                        "claim-only claimed recovery has a recorded backend result"
                    )
                if self.connection.execute(
                    """
                    SELECT 1 FROM execution_verifications
                    WHERE lineage_id = ? AND work_fingerprint = ?
                      AND fencing_token = ?
                    LIMIT 1
                    """,
                    (budget.lineage_id, fingerprint, current_token),
                ).fetchone() is not None:
                    raise ValueError(
                        "claim-only claimed recovery has verification history"
                    )
                if self.connection.execute(
                    """
                    SELECT 1 FROM execution_reconciliations
                    WHERE lineage_id = ? AND work_fingerprint = ?
                      AND fencing_token = ?
                    LIMIT 1
                    """,
                    (budget.lineage_id, fingerprint, current_token),
                ).fetchone() is not None:
                    raise ValueError(
                        "claim-only claimed recovery has reconciliation history"
                    )

                budget_after = BudgetEnvelope(
                    lineage_id=budget.lineage_id,
                    max_depth=max_depth,
                    depth=depth,
                    remaining_children=remaining_children,
                    remaining_active=remaining_active,
                    remaining_retries=remaining_retries,
                    remaining_backend_jobs=remaining_backend_jobs,
                    scope_id=budget.scope_id,
                )
                claimed_work = WorkUnit(
                    id=work.id,
                    root_frontier_id=work.root_frontier_id,
                    parent_work_id=work.parent_work_id,
                    inputs=work.inputs,
                    operation=work.operation,
                    required_capabilities=work.required_capabilities,
                    collision_keys=work.collision_keys,
                    recursion_depth=work.recursion_depth,
                    budget_allocation=work.budget_allocation,
                    expected_outputs=work.expected_outputs,
                    completion_criteria=work.completion_criteria,
                    status=WorkUnitStatus.CLAIMED,
                    payload=work.payload,
                )

                if current_holder is not None and now < current_expiry:
                    if str(current_holder) != holder:
                        raise ValueError(
                            "claim-only work already has an active lease"
                        )
                    self.connection.commit()
                    return DurableDispatchAdmission(
                        work=claimed_work,
                        lease=Lease(
                            work_fingerprint=fingerprint,
                            holder=holder,
                            fencing_token=current_token,
                            expires_at=current_expiry,
                        ),
                        budget_after=budget_after,
                        budget_generation=budget_generation,
                        work_generation=work_generation,
                        retry_consumed=0,
                    )

                reconciliation_evidence = (
                    "claim-only execution_authority=false",
                    "claim-only work remained CLAIMED",
                    "no backend result or verification was recorded",
                )
                reconciliation_reason = (
                    "expired claim-only lease is safe to re-fence without "
                    "backend re-execution"
                )
                reconciliation_sha256 = _reconciliation_digest(
                    outcome="NO_EFFECT_CONFIRMED",
                    reason=reconciliation_reason,
                    evidence=reconciliation_evidence,
                    reconciler="portfolio-claim-recovery",
                    observed_at=now,
                )
                self.connection.execute(
                    """
                    INSERT INTO execution_reconciliations (
                        lineage_id, work_fingerprint, fencing_token, sequence,
                        outcome, reason, evidence_json, reconciler, observed_at,
                        reconciliation_sha256
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        budget.lineage_id,
                        fingerprint,
                        current_token,
                        "NO_EFFECT_CONFIRMED",
                        reconciliation_reason,
                        _canonical_json(list(reconciliation_evidence)),
                        "portfolio-claim-recovery",
                        now,
                        reconciliation_sha256,
                    ),
                )

                new_token = current_token + 1
                expires_at = now + ttl
                lease_update = self.connection.execute(
                    """
                    UPDATE leases
                    SET holder = ?, fencing_token = ?, expires_at = ?, completed = 0
                    WHERE work_fingerprint = ? AND fencing_token = ?
                    """,
                    (holder, new_token, expires_at, fingerprint, current_token),
                )
                if lease_update.rowcount != 1:
                    raise ValueError("claim-only recovery fence changed concurrently")

                new_work_generation = work_generation + 1
                work_update = self.connection.execute(
                    """
                    UPDATE recursive_work_state
                    SET generation = generation + 1
                    WHERE lineage_id = ? AND work_fingerprint = ?
                      AND status = ? AND generation = ?
                    """,
                    (
                        budget.lineage_id,
                        fingerprint,
                        WorkUnitStatus.CLAIMED.value,
                        work_generation,
                    ),
                )
                if work_update.rowcount != 1:
                    raise ValueError("claim-only recovery work changed concurrently")

                attempt_sha256 = _attempt_digest(
                    lineage_id=budget.lineage_id,
                    work_fingerprint_value=fingerprint,
                    fencing_token=new_token,
                    holder=holder,
                    admitted_at=now,
                    budget_generation=budget_generation,
                    work_generation=new_work_generation,
                )
                self.connection.execute(
                    """
                    INSERT INTO execution_attempts (
                        lineage_id, work_fingerprint, fencing_token, holder,
                        admitted_at, budget_generation, work_generation,
                        attempt_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        budget.lineage_id,
                        fingerprint,
                        new_token,
                        holder,
                        now,
                        budget_generation,
                        new_work_generation,
                        attempt_sha256,
                    ),
                )
                self.connection.commit()
                return DurableDispatchAdmission(
                    work=claimed_work,
                    lease=Lease(
                        work_fingerprint=fingerprint,
                        holder=holder,
                        fencing_token=new_token,
                        expires_at=expires_at,
                    ),
                    budget_after=budget_after,
                    budget_generation=budget_generation,
                    work_generation=new_work_generation,
                    retry_consumed=0,
                )
            else:
                raise ValueError(
                    "claim-only recovery requires PENDING or CLAIMED work; "
                    f"found {current_status.value}"
                )
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

        assert pending_generations is not None
        return self.admit(
            lineage_id=budget.lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=budget.scope_id,
            expected_budget_generation=pending_generations[0],
            expected_work_generation=pending_generations[1],
            holder=holder,
            now=now,
            ttl=ttl,
        )


    def _attempt_row(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
    ):
        row = self.connection.execute(
            """
            SELECT holder, admitted_at, budget_generation, work_generation,
                   attempt_sha256, result_json, result_sha256, result_recorded_at
            FROM execution_attempts
            WHERE lineage_id = ?
              AND work_fingerprint = ?
              AND fencing_token = ?
            """,
            (lineage_id, work_fingerprint_value, fencing_token),
        ).fetchone()
        if row is None:
            raise KeyError((lineage_id, work_fingerprint_value, fencing_token))
        (
            holder,
            admitted_at,
            budget_generation,
            work_generation,
            attempt_sha256,
            result_json,
            result_sha256,
            result_recorded_at,
        ) = row
        expected_attempt_sha256 = _attempt_digest(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            holder=str(holder),
            admitted_at=float(admitted_at),
            budget_generation=int(budget_generation),
            work_generation=int(work_generation),
        )
        if not hmac.compare_digest(str(attempt_sha256), expected_attempt_sha256):
            raise ValueError("execution attempt journal digest mismatch")
        return (
            str(holder),
            float(admitted_at),
            int(budget_generation),
            int(work_generation),
            str(attempt_sha256),
            str(result_json) if result_json is not None else None,
            str(result_sha256) if result_sha256 is not None else None,
            float(result_recorded_at) if result_recorded_at is not None else None,
        )

    def record_result(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
        result: BackendResult,
        recorded_at: float,
    ) -> None:
        if result.work_fingerprint != work_fingerprint_value:
            raise ValueError("backend result fingerprint does not match execution attempt")
        payload = _result_payload(result)
        payload_sha256 = _sha256_text(payload)

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_row(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            existing_json = row[5]
            existing_sha256 = row[6]
            latest_reconciliation = self.connection.execute(
                """
                SELECT outcome
                FROM execution_reconciliations
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()
            if (
                latest_reconciliation is not None
                and str(latest_reconciliation[0]) == "NO_EFFECT_CONFIRMED"
            ):
                raise ValueError(
                    "execution result cannot follow conclusive no-effect reconciliation"
                )
            if existing_json is not None or existing_sha256 is not None:
                if (
                    existing_json == payload
                    and existing_sha256 is not None
                    and hmac.compare_digest(existing_sha256, payload_sha256)
                ):
                    self.connection.rollback()
                    return
                raise ValueError("execution attempt already has a different result")

            updated = self.connection.execute(
                """
                UPDATE execution_attempts
                SET result_json = ?, result_sha256 = ?, result_recorded_at = ?
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                  AND result_json IS NULL
                  AND result_sha256 IS NULL
                """,
                (
                    payload,
                    payload_sha256,
                    recorded_at,
                    lineage_id,
                    work_fingerprint_value,
                    fencing_token,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("execution result journal changed during recording")
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def load_result(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
    ) -> BackendResult | None:
        row = self._attempt_row(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        payload = row[5]
        payload_sha256 = row[6]
        if payload is None and payload_sha256 is None:
            return None
        if payload is None or payload_sha256 is None:
            raise ValueError("execution result journal is incomplete")
        expected = _sha256_text(payload)
        if not hmac.compare_digest(payload_sha256, expected):
            raise ValueError("execution result journal digest mismatch")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("execution result journal is invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("execution result journal payload is invalid")
        result = BackendResult(
            work_fingerprint=str(data["work_fingerprint"]),
            succeeded=bool(data["succeeded"]),
            outputs=tuple(str(item) for item in data["outputs"]),
            evidence=tuple(str(item) for item in data["evidence"]),
            classification=str(data["classification"]),
        )
        if result.work_fingerprint != work_fingerprint_value:
            raise ValueError("execution result journal fingerprint mismatch")
        return result

    def load_latest_verification(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
    ) -> DurableExecutionVerification | None:
        self._attempt_row(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        row = self.connection.execute(
            """
            SELECT sequence, status, reason, verified_at, verification_sha256
            FROM execution_verifications
            WHERE lineage_id = ?
              AND work_fingerprint = ?
              AND fencing_token = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (
                lineage_id,
                work_fingerprint_value,
                fencing_token,
            ),
        ).fetchone()
        if row is None:
            return None
        status = WorkUnitStatus(str(row[1]))
        expected = _verification_digest(
            status=status,
            reason=str(row[2]),
            verified_at=float(row[3]),
        )
        if not hmac.compare_digest(str(row[4]), expected):
            raise ValueError("execution verification journal digest mismatch")
        return DurableExecutionVerification(
            sequence=int(row[0]),
            status=status,
            reason=str(row[2]),
            verified_at=float(row[3]),
            sha256=str(row[4]),
        )


    def load_latest_reconciliation(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
    ) -> DurableExecutionReconciliation | None:
        self._attempt_row(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
        row = self.connection.execute(
            """
            SELECT sequence, outcome, reason, evidence_json, reconciler,
                   observed_at, reconciliation_sha256
            FROM execution_reconciliations
            WHERE lineage_id = ?
              AND work_fingerprint = ?
              AND fencing_token = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (
                lineage_id,
                work_fingerprint_value,
                fencing_token,
            ),
        ).fetchone()
        if row is None:
            return None
        try:
            evidence_data = json.loads(str(row[3]))
        except json.JSONDecodeError as exc:
            raise ValueError(
                "execution reconciliation evidence is invalid JSON"
            ) from exc
        if not isinstance(evidence_data, list):
            raise ValueError(
                "execution reconciliation evidence is structurally invalid"
            )
        evidence = tuple(str(item) for item in evidence_data)
        expected = _reconciliation_digest(
            outcome=str(row[1]),
            reason=str(row[2]),
            evidence=evidence,
            reconciler=str(row[4]),
            observed_at=float(row[5]),
        )
        if not hmac.compare_digest(str(row[6]), expected):
            raise ValueError("execution reconciliation journal digest mismatch")
        return DurableExecutionReconciliation(
            sequence=int(row[0]),
            outcome=str(row[1]),
            reason=str(row[2]),
            evidence=evidence,
            reconciler=str(row[4]),
            observed_at=float(row[5]),
            sha256=str(row[6]),
        )


    def record_verification(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
        status: WorkUnitStatus,
        reason: str,
        verified_at: float,
    ) -> int:
        if status in _TERMINAL_STATUSES:
            raise ValueError(
                "terminal verification requires atomic finalization"
            )
        if status is not WorkUnitStatus.OUTCOME_UNKNOWN:
            raise ValueError(
                "direct verification recording is limited to OUTCOME_UNKNOWN"
            )
        if not reason.strip():
            raise ValueError("execution verification reason is required")
        verification_sha256 = _verification_digest(
            status=status,
            reason=reason,
            verified_at=verified_at,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self._attempt_row(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            if row[5] is None or row[6] is None:
                raise ValueError("execution result must be recorded before verification")

            latest = self.connection.execute(
                """
                SELECT sequence, status, reason, verified_at, verification_sha256
                FROM execution_verifications
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()
            if latest is not None:
                (
                    latest_sequence,
                    latest_status,
                    latest_reason,
                    latest_verified_at,
                    latest_sha256,
                ) = latest
                latest_status_value = WorkUnitStatus(str(latest_status))
                latest_expected = _verification_digest(
                    status=latest_status_value,
                    reason=str(latest_reason),
                    verified_at=float(latest_verified_at),
                )
                if not hmac.compare_digest(str(latest_sha256), latest_expected):
                    raise ValueError("execution verification journal digest mismatch")
                if (
                    latest_status_value is status
                    and str(latest_reason) == reason
                    and float(latest_verified_at) == verified_at
                    and hmac.compare_digest(str(latest_sha256), verification_sha256)
                ):
                    self.connection.rollback()
                    return int(latest_sequence)
                if latest_status_value is not WorkUnitStatus.OUTCOME_UNKNOWN:
                    raise ValueError("execution attempt already has terminal verification")
                sequence = int(latest_sequence) + 1
            else:
                sequence = 1

            self.connection.execute(
                """
                INSERT INTO execution_verifications (
                    lineage_id, work_fingerprint, fencing_token, sequence,
                    status, reason, verified_at, verification_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lineage_id,
                    work_fingerprint_value,
                    fencing_token,
                    sequence,
                    status.value,
                    reason,
                    verified_at,
                    verification_sha256,
                ),
            )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        return sequence

    def begin_verification(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
        expected_work_generation: int,
        lease: Lease,
        started_at: float,
    ) -> int:
        """Atomically move observed RUNNING work into VERIFYING under the same fence.

        Replay is idempotent if the exact work is already VERIFYING at the one
        expected successor generation.
        """
        if lease.fencing_token != fencing_token:
            raise ValueError("execution verification fencing token mismatch")

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            attempt = self._attempt_row(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            if attempt[5] is None or attempt[6] is None:
                raise ValueError(
                    "execution result must be recorded before verification"
                )

            work_row = self.connection.execute(
                """
                SELECT status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if work_row is None:
                raise ValueError("recursive work state not found")
            current_status = WorkUnitStatus(str(work_row[0]))
            current_generation = int(work_row[1])

            _validate_active_lease_state(
                self.connection,
                work_fingerprint_value=work_fingerprint_value,
                lease=lease,
                now=started_at,
            )

            if (
                current_status is WorkUnitStatus.VERIFYING
                and current_generation == expected_work_generation + 1
            ):
                self.connection.rollback()
                return current_generation

            if current_generation != expected_work_generation:
                raise ValueError("recursive work generation mismatch")
            if current_status is not WorkUnitStatus.RUNNING:
                raise ValueError(
                    "verification start requires RUNNING work"
                )
            allowed = _ALLOWED_STATUS_TRANSITIONS.get(
                current_status,
                frozenset(),
            )
            if WorkUnitStatus.VERIFYING not in allowed:
                raise ValueError(
                    "recursive work lifecycle transition is invalid: "
                    f"{current_status.value} -> VERIFYING"
                )

            updated = self.connection.execute(
                """
                UPDATE recursive_work_state
                SET status = ?, generation = generation + 1
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND status = ?
                  AND generation = ?
                """,
                (
                    WorkUnitStatus.VERIFYING.value,
                    lineage_id,
                    work_fingerprint_value,
                    WorkUnitStatus.RUNNING.value,
                    expected_work_generation,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "recursive work changed during verification start"
                )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        return expected_work_generation + 1


    def finalize_terminal_verification(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
        expected_work_generation: int,
        lease: Lease,
        status: WorkUnitStatus,
        reason: str,
        verified_at: float,
    ) -> int:
        """Atomically finalize recursive work, its lease, and terminal evidence."""
        if status not in _TERMINAL_STATUSES:
            raise ValueError("atomic verification finalization requires terminal status")
        if not reason.strip():
            raise ValueError("execution verification reason is required")
        if lease.fencing_token != fencing_token:
            raise ValueError("execution verification fencing token mismatch")

        verification_sha256 = _verification_digest(
            status=status,
            reason=reason,
            verified_at=verified_at,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")

            attempt = self._attempt_row(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            if attempt[5] is None or attempt[6] is None:
                raise ValueError(
                    "execution result must be recorded before terminal finalization"
                )

            work_row = self.connection.execute(
                """
                SELECT status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if work_row is None:
                raise ValueError("recursive work state not found")

            current_status = WorkUnitStatus(str(work_row[0]))
            current_generation = int(work_row[1])
            if current_generation != expected_work_generation:
                raise ValueError("recursive work generation mismatch")
            if current_status in _TERMINAL_STATUSES:
                raise ValueError(
                    "terminal recursive work state already finalized"
                )

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
                now=verified_at,
            )

            latest = self.connection.execute(
                """
                SELECT sequence, status, reason, verified_at, verification_sha256
                FROM execution_verifications
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()
            if latest is None:
                sequence = 1
            else:
                (
                    latest_sequence,
                    latest_status,
                    latest_reason,
                    latest_verified_at,
                    latest_sha256,
                ) = latest
                latest_status_value = WorkUnitStatus(str(latest_status))
                latest_expected = _verification_digest(
                    status=latest_status_value,
                    reason=str(latest_reason),
                    verified_at=float(latest_verified_at),
                )
                if not hmac.compare_digest(str(latest_sha256), latest_expected):
                    raise ValueError(
                        "execution verification journal digest mismatch"
                    )
                if latest_status_value is not WorkUnitStatus.OUTCOME_UNKNOWN:
                    raise ValueError(
                        "execution attempt already has terminal verification"
                    )
                sequence = int(latest_sequence) + 1

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
                    "recursive work lease changed during atomic verification finalization"
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
                    expected_work_generation,
                ),
            )
            if work_update.rowcount != 1:
                raise ValueError(
                    "recursive work generation changed during atomic verification finalization"
                )

            self.connection.execute(
                """
                INSERT INTO execution_verifications (
                    lineage_id, work_fingerprint, fencing_token, sequence,
                    status, reason, verified_at, verification_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lineage_id,
                    work_fingerprint_value,
                    fencing_token,
                    sequence,
                    status.value,
                    reason,
                    verified_at,
                    verification_sha256,
                ),
            )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        return expected_work_generation + 1


    def reconcile_admitted(
        self,
        *,
        lineage_id: str,
        work_fingerprint_value: str,
        fencing_token: int,
        expected_work_generation: int,
        lease: Lease,
        outcome: str,
        reason: str,
        evidence: tuple[str, ...],
        reconciler: str,
        observed_at: float,
        allow_recorded_outcome_unknown: bool = False,
    ) -> int:
        """Reconcile an ADMITTED attempt without executing the backend.

        By default this is valid only before result recording. A caller may
        explicitly allow reconciliation of a recorded failed OUTCOME_UNKNOWN;
        no other recorded result is eligible.

        Only NO_EFFECT_CONFIRMED may atomically release the exact fence and move
        work to FAILED_RETRYABLE. INDETERMINATE and EFFECT_CONFIRMED remain
        non-retryable. A prior INDETERMINATE observation may be refined, but a
        conclusive reconciliation cannot be changed.
        """
        if outcome not in _RECONCILIATION_OUTCOMES:
            raise ValueError("unsupported execution reconciliation outcome")
        if not reason.strip():
            raise ValueError("execution reconciliation reason is required")
        if not reconciler.strip():
            raise ValueError("execution reconciler identity is required")
        normalized_evidence = tuple(str(item).strip() for item in evidence)
        if not normalized_evidence or any(not item for item in normalized_evidence):
            raise ValueError("execution reconciliation evidence is required")
        if lease.fencing_token != fencing_token:
            raise ValueError("execution reconciliation fencing token mismatch")

        reconciliation_sha256 = _reconciliation_digest(
            outcome=outcome,
            reason=reason,
            evidence=normalized_evidence,
            reconciler=reconciler,
            observed_at=observed_at,
        )

        try:
            self.connection.execute("BEGIN IMMEDIATE")
            attempt = self._attempt_row(
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            if attempt[5] is not None or attempt[6] is not None:
                if not allow_recorded_outcome_unknown:
                    raise ValueError(
                        "ADMITTED reconciliation is invalid after result recording"
                    )
                recorded = self.load_result(
                    lineage_id=lineage_id,
                    work_fingerprint_value=work_fingerprint_value,
                    fencing_token=fencing_token,
                )
                if (
                    recorded is None
                    or recorded.succeeded
                    or recorded.classification != "OUTCOME_UNKNOWN"
                ):
                    raise ValueError(
                        "recorded reconciliation is limited to OUTCOME_UNKNOWN"
                    )

            latest = self.connection.execute(
                """
                SELECT sequence, outcome, reason, evidence_json, reconciler,
                       observed_at, reconciliation_sha256
                FROM execution_reconciliations
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()
            sequence = 1
            if latest is not None:
                (
                    latest_sequence,
                    latest_outcome,
                    latest_reason,
                    latest_evidence_json,
                    latest_reconciler,
                    latest_observed_at,
                    latest_sha256,
                ) = latest
                try:
                    latest_evidence_data = json.loads(str(latest_evidence_json))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "execution reconciliation evidence is invalid JSON"
                    ) from exc
                if not isinstance(latest_evidence_data, list):
                    raise ValueError(
                        "execution reconciliation evidence is structurally invalid"
                    )
                latest_evidence = tuple(str(item) for item in latest_evidence_data)
                latest_expected = _reconciliation_digest(
                    outcome=str(latest_outcome),
                    reason=str(latest_reason),
                    evidence=latest_evidence,
                    reconciler=str(latest_reconciler),
                    observed_at=float(latest_observed_at),
                )
                if not hmac.compare_digest(str(latest_sha256), latest_expected):
                    raise ValueError("execution reconciliation journal digest mismatch")
                if (
                    str(latest_outcome) == outcome
                    and str(latest_reason) == reason
                    and latest_evidence == normalized_evidence
                    and str(latest_reconciler) == reconciler
                    and float(latest_observed_at) == observed_at
                    and hmac.compare_digest(
                        str(latest_sha256), reconciliation_sha256
                    )
                ):
                    current = self.connection.execute(
                        """
                        SELECT generation
                        FROM recursive_work_state
                        WHERE lineage_id = ? AND work_fingerprint = ?
                        """,
                        (lineage_id, work_fingerprint_value),
                    ).fetchone()
                    self.connection.rollback()
                    if current is None:
                        raise ValueError("recursive work state not found")
                    return int(current[0])
                if str(latest_outcome) != "INDETERMINATE":
                    raise ValueError(
                        "conclusive execution reconciliation cannot be changed"
                    )
                sequence = int(latest_sequence) + 1

            work_row = self.connection.execute(
                """
                SELECT status, generation
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, work_fingerprint_value),
            ).fetchone()
            if work_row is None:
                raise ValueError("recursive work state not found")
            current_status = WorkUnitStatus(str(work_row[0]))
            current_generation = int(work_row[1])
            if current_generation != expected_work_generation:
                raise ValueError("recursive work generation mismatch")
            if current_status not in {
                WorkUnitStatus.CLAIMED,
                WorkUnitStatus.RUNNING,
            }:
                raise ValueError(
                    "ADMITTED reconciliation requires claimed or running work"
                )

            lease_row = self.connection.execute(
                """
                SELECT holder, fencing_token, completed
                FROM leases
                WHERE work_fingerprint = ?
                """,
                (work_fingerprint_value,),
            ).fetchone()
            if lease_row is None:
                raise ValueError("execution reconciliation lease state not found")
            if (
                lease_row[0] != lease.holder
                or int(lease_row[1]) != fencing_token
                or bool(lease_row[2])
            ):
                raise ValueError(
                    "execution reconciliation fence is no longer current"
                )

            evidence_json = _canonical_json(list(normalized_evidence))
            self.connection.execute(
                """
                INSERT INTO execution_reconciliations (
                    lineage_id, work_fingerprint, fencing_token, sequence,
                    outcome, reason, evidence_json, reconciler,
                    observed_at, reconciliation_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lineage_id,
                    work_fingerprint_value,
                    fencing_token,
                    sequence,
                    outcome,
                    reason,
                    evidence_json,
                    reconciler,
                    observed_at,
                    reconciliation_sha256,
                ),
            )

            if outcome != "NO_EFFECT_CONFIRMED":
                self.connection.commit()
                return current_generation

            allowed = _ALLOWED_STATUS_TRANSITIONS.get(
                current_status,
                frozenset(),
            )
            if WorkUnitStatus.FAILED_RETRYABLE not in allowed:
                raise ValueError(
                    "reconciled no-effect work cannot enter retryable state"
                )

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
                    fencing_token,
                ),
            )
            if lease_update.rowcount != 1:
                raise ValueError(
                    "execution reconciliation lease changed during finalization"
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
                    WorkUnitStatus.FAILED_RETRYABLE.value,
                    lineage_id,
                    work_fingerprint_value,
                    expected_work_generation,
                ),
            )
            if work_update.rowcount != 1:
                raise ValueError(
                    "recursive work generation changed during reconciliation"
                )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

        return expected_work_generation + 1

    def unresolved_attempts(self) -> tuple[DurableExecutionJournalEntry, ...]:
        rows = self.connection.execute(
            """
            SELECT lineage_id, work_fingerprint, fencing_token, holder,
                   admitted_at, budget_generation, work_generation,
                   attempt_sha256, result_json, result_sha256
            FROM execution_attempts
            ORDER BY admitted_at, lineage_id, work_fingerprint, fencing_token
            """
        ).fetchall()
        unresolved: list[DurableExecutionJournalEntry] = []
        for (
            lineage_id,
            work_fingerprint_value,
            fencing_token,
            holder,
            admitted_at,
            budget_generation,
            work_generation,
            attempt_sha256,
            result_json,
            result_sha256,
        ) in rows:
            expected_attempt_sha256 = _attempt_digest(
                lineage_id=str(lineage_id),
                work_fingerprint_value=str(work_fingerprint_value),
                fencing_token=int(fencing_token),
                holder=str(holder),
                admitted_at=float(admitted_at),
                budget_generation=int(budget_generation),
                work_generation=int(work_generation),
            )
            if not hmac.compare_digest(str(attempt_sha256), expected_attempt_sha256):
                raise ValueError("execution attempt journal digest mismatch")

            latest = self.connection.execute(
                """
                SELECT status, reason, verified_at, verification_sha256
                FROM execution_verifications
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()

            last_status = None
            last_reason = None
            if latest is not None:
                last_status = WorkUnitStatus(str(latest[0]))
                last_reason = str(latest[1])
                latest_expected = _verification_digest(
                    status=last_status,
                    reason=last_reason,
                    verified_at=float(latest[2]),
                )
                if not hmac.compare_digest(str(latest[3]), latest_expected):
                    raise ValueError("execution verification journal digest mismatch")

            latest_reconciliation = self.connection.execute(
                """
                SELECT outcome, reason, evidence_json, reconciler,
                       observed_at, reconciliation_sha256
                FROM execution_reconciliations
                WHERE lineage_id = ?
                  AND work_fingerprint = ?
                  AND fencing_token = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (lineage_id, work_fingerprint_value, fencing_token),
            ).fetchone()
            last_reconciliation_outcome = None
            last_reconciliation_reason = None
            if latest_reconciliation is not None:
                (
                    raw_outcome,
                    raw_reconciliation_reason,
                    raw_evidence_json,
                    raw_reconciler,
                    raw_observed_at,
                    raw_reconciliation_sha256,
                ) = latest_reconciliation
                try:
                    evidence_data = json.loads(str(raw_evidence_json))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "execution reconciliation evidence is invalid JSON"
                    ) from exc
                if not isinstance(evidence_data, list):
                    raise ValueError(
                        "execution reconciliation evidence is structurally invalid"
                    )
                reconciliation_evidence = tuple(str(item) for item in evidence_data)
                reconciliation_expected = _reconciliation_digest(
                    outcome=str(raw_outcome),
                    reason=str(raw_reconciliation_reason),
                    evidence=reconciliation_evidence,
                    reconciler=str(raw_reconciler),
                    observed_at=float(raw_observed_at),
                )
                if not hmac.compare_digest(
                    str(raw_reconciliation_sha256),
                    reconciliation_expected,
                ):
                    raise ValueError(
                        "execution reconciliation journal digest mismatch"
                    )
                last_reconciliation_outcome = str(raw_outcome)
                last_reconciliation_reason = str(raw_reconciliation_reason)

            if result_json is None and result_sha256 is None:
                if last_reconciliation_outcome == "NO_EFFECT_CONFIRMED":
                    continue
                phase = (
                    "EFFECT_CONFIRMED"
                    if last_reconciliation_outcome == "EFFECT_CONFIRMED"
                    else "ADMITTED"
                )
            elif result_json is None or result_sha256 is None:
                raise ValueError("execution result journal is incomplete")
            elif last_status is None:
                phase = "RESULT_RECORDED"
            elif last_status is WorkUnitStatus.OUTCOME_UNKNOWN:
                phase = "OUTCOME_UNKNOWN"
            else:
                continue

            if result_json is not None:
                expected_result_sha256 = _sha256_text(str(result_json))
                if not hmac.compare_digest(str(result_sha256), expected_result_sha256):
                    raise ValueError("execution result journal digest mismatch")

            unresolved.append(
                DurableExecutionJournalEntry(
                    lineage_id=str(lineage_id),
                    work_fingerprint=str(work_fingerprint_value),
                    fencing_token=int(fencing_token),
                    holder=str(holder),
                    admitted_at=float(admitted_at),
                    budget_generation=int(budget_generation),
                    work_generation=int(work_generation),
                    phase=phase,
                    result_sha256=(
                        str(result_sha256) if result_sha256 is not None else None
                    ),
                    last_verification_status=last_status,
                    last_verification_reason=last_reason,
                    last_reconciliation_outcome=last_reconciliation_outcome,
                    last_reconciliation_reason=last_reconciliation_reason,
                )
            )
        return tuple(unresolved)


def execute_admitted(
    *,
    frontier: Frontier,
    admission: DurableDispatchAdmission,
    backend: ExecutionBackend,
    journal: SqliteDispatchAdmissionStore,
    recorded_at: float,
) -> DispatchAttempt:
    """Execute one durably admitted work unit and persist its observed result.

    The admission row already exists before backend execution. If the process dies
    after a downstream effect but before record_result(), restart sees phase
    ADMITTED and must reconcile rather than blindly re-execute. Once the result is
    recorded, restart can re-verify without executing the backend again.
    """
    if admission.work.payload.get("execution_authority") is False:
        raise ValueError("durable admission does not authorize backend execution")
    result = backend.execute(admission.work)
    journal.record_result(
        lineage_id=admission.budget_after.lineage_id,
        work_fingerprint_value=work_unit_fingerprint(admission.work),
        fencing_token=admission.lease.fencing_token,
        result=result,
        recorded_at=recorded_at,
    )
    return DispatchAttempt(
        frontier=frontier,
        work=admission.work,
        lease=admission.lease,
        result=result,
    )


def verify_observed_attempt(
    attempt: DispatchAttempt,
    *,
    lease_store: LeaseStore,
    now: float,
    current_subject_reader: SubjectReader,
    evidence_verifier: EvidenceVerifier,
    manage_lease: bool = True,
) -> VerificationOutcome:
    """Verify an observed result without promoting durable terminal state.

    Terminal work state, lease state, and terminal verification evidence must be
    committed together by finalize_terminal_verification().
    """
    return verify_attempt(
        attempt,
        lease_store=lease_store,
        now=now,
        current_subject_reader=current_subject_reader,
        evidence_verifier=evidence_verifier,
        manage_lease=manage_lease,
    )
