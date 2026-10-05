from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Iterable

from .dedup import frontier_fingerprint
from .github_backend import (
    GitHubBackend,
    GitHubOperation,
    GitHubRestTransport,
    GitHubTransport,
    TargetAuthorityGrant,
)
from .m6_github import frontier_to_github_inspection_work
from .models import (
    ExactSubject,
    Frontier,
    ProjectDefinition,
    ProjectExecutionTarget,
    ReplayPolicy,
    WorkerDefinition,
)
from .operator import InspectionRunResult, run_durable_github_read_inspection
from .recursive_state import SqliteRecursiveWorkStore
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint
from .worker_routing import (
    ReadOnlyWorkerRoute,
    WorkerRouteEnvelope,
    enqueue_worker_route_record,
    ensure_worker_route_schema,
    resolve_read_only_worker_route,
)


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_READ_ONLY_WORK_TYPES = frozenset(
    {"INSPECT", "RETEST", "REREVIEW", "REQUALIFY"}
)
_TERMINAL_QUEUE_STATES = frozenset(
    {"COMPLETE", "FAILED_DETERMINISTIC", "SUPERSEDED"}
)
_RESERVED_QUEUE_STATES = frozenset(
    {"CLAIMED", "OUTCOME_UNKNOWN", "ROUTED"}
)
_NONCLAIMABLE_QUEUE_STATES = _TERMINAL_QUEUE_STATES | frozenset(
    {"OUTCOME_UNKNOWN", "ROUTED"}
)


@dataclass(frozen=True)
class QueueClaim:
    snapshot_id: int
    frontier_fingerprint: str
    frontier: Frontier
    frontier_json: str
    holder: str
    fencing_token: int
    expires_at: float
    target_repository: str
    target_ref: str
    target_head: str | None
    operator_lineage: str
    attempt_generation: int


@dataclass(frozen=True)
class QueueConsumptionResult:
    claimed: bool
    queue_state: str
    snapshot_id: int | None = None
    frontier_fingerprint: str | None = None
    fencing_token: int | None = None
    operator_status: str | None = None
    route_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class QueueReconciliationResult:
    snapshot_id: int
    frontier_fingerprint: str
    previous_state: str
    final_state: str
    fencing_token: int
    attempt_generation: int
    resolution: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_queue_claims (
    snapshot_id INTEGER NOT NULL,
    frontier_fingerprint TEXT NOT NULL,
    holder TEXT,
    fencing_token INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL,
    target_repository TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    target_head TEXT,
    operator_lineage TEXT NOT NULL,
    attempt_generation INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (snapshot_id, frontier_fingerprint),
    FOREIGN KEY (snapshot_id, frontier_fingerprint)
        REFERENCES portfolio_frontiers(snapshot_id, frontier_fingerprint)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portfolio_queue_reconciliations (
    reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    frontier_fingerprint TEXT NOT NULL,
    expected_fencing_token INTEGER NOT NULL,
    previous_state TEXT NOT NULL,
    resolution TEXT NOT NULL,
    final_state TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    reconciler TEXT NOT NULL,
    reconciled_at REAL NOT NULL
);
"""


def _ensure_queue_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(portfolio_queue_claims)")
    }
    if "attempt_generation" not in columns:
        connection.execute(
            """
            ALTER TABLE portfolio_queue_claims
            ADD COLUMN attempt_generation INTEGER NOT NULL DEFAULT 1
            """
        )


def _canonical_collision_keys(raw: str) -> frozenset[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("queued frontier collision keys are structurally invalid")
    return frozenset(value)


def _execution_target(
    projects: tuple[ProjectDefinition, ...],
    frontier: Frontier,
) -> ProjectExecutionTarget:
    project = next((item for item in projects if item.id == frontier.project), None)
    if project is None:
        raise ValueError("queued frontier project is absent from current registry")
    matches = tuple(
        item
        for item in project.execution_targets
        if item.work_type == frontier.work_type
    )
    if len(matches) != 1:
        raise ValueError("queued frontier lacks one exact execution target")
    return matches[0]


def _project_for_frontier(
    projects: tuple[ProjectDefinition, ...],
    frontier: Frontier,
) -> ProjectDefinition:
    project = next((item for item in projects if item.id == frontier.project), None)
    if project is None:
        raise ValueError("queued frontier project is absent from current registry")
    return project


def _packet_sensitivity(
    projects: tuple[ProjectDefinition, ...],
    frontier: Frontier,
) -> str:
    consumer = _project_for_frontier(projects, frontier)
    provider_owners = tuple(
        project
        for project in projects
        if frontier.subject.repository in project.repositories
    )
    if not provider_owners:
        return "PRIVATE"
    if consumer.visibility == "private":
        return "PRIVATE"
    if any(project.visibility == "private" for project in provider_owners):
        return "PRIVATE"
    return "PUBLIC"


def _expected_lineage(
    snapshot_id: int,
    fingerprint: str,
    attempt_generation: int,
) -> str:
    if attempt_generation <= 0:
        raise ValueError("queue attempt generation must be positive")
    if attempt_generation == 1:
        return f"queue:{snapshot_id}:{fingerprint}"
    return f"queue:{snapshot_id}:{fingerprint}:retry:{attempt_generation}"


def _frontier_from_json(
    *,
    fingerprint: str,
    frontier_json: str | None,
) -> tuple[Frontier, str]:
    if frontier_json is None:
        raise ValueError("queued frontier predates reconstructable frontier state")
    frontier_json_text = str(frontier_json)
    raw = json.loads(frontier_json_text)
    if not isinstance(raw, dict):
        raise ValueError("queued frontier payload is structurally invalid")
    frontier = Frontier.from_mapping(raw)
    if frontier_fingerprint(frontier) != fingerprint:
        raise ValueError("queued frontier fingerprint does not match durable payload")
    return frontier, frontier_json_text


class SqliteQueueStore:
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
        self.connection.executescript(_SCHEMA)
        _ensure_queue_columns(self.connection)
        ensure_worker_route_schema(self.connection)

    def close(self) -> None:
        self.connection.close()

    def _claim_row(
        self,
        *,
        fingerprint: str,
        frontier_json: str | None,
        projects: tuple[ProjectDefinition, ...],
    ) -> tuple[Frontier, ProjectExecutionTarget, str]:
        frontier, frontier_json_text = _frontier_from_json(
            fingerprint=fingerprint,
            frontier_json=frontier_json,
        )
        target = _execution_target(projects, frontier)
        return frontier, target, frontier_json_text

    def claim_next(
        self,
        *,
        projects: Iterable[ProjectDefinition],
        registry_digest: str,
        dependency_digest: str,
        worker_registry_digest: str = "0000000000000000000000000000000000000000000000000000000000000000",
        holder: str,
        now: float,
        ttl: float,
        supported_work_types: frozenset[str] = _SUPPORTED_READ_ONLY_WORK_TYPES,
    ) -> QueueClaim | None:
        if not holder.strip():
            raise ValueError("queue lease holder is required")
        if ttl <= 0:
            raise ValueError("queue lease ttl must be positive")
        project_tuple = tuple(projects)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            latest = self.connection.execute(
                """
                SELECT snapshot_id
                FROM portfolio_snapshots
                WHERE registry_digest = ?
                  AND dependency_digest = ?
                  AND worker_registry_digest = ?
                ORDER BY snapshot_id DESC
                LIMIT 1
                """,
                (
                    registry_digest,
                    dependency_digest,
                    worker_registry_digest,
                ),
            ).fetchone()
            if latest is None:
                self.connection.commit()
                return None
            latest_snapshot_id = int(latest[0])

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
                    (latest_snapshot_id,),
                ).fetchall()
            }

            reservation_rows = self.connection.execute(
                """
                SELECT
                    qc.snapshot_id,
                    pf.frontier_fingerprint,
                    pf.collision_keys_json,
                    pf.frontier_json
                FROM portfolio_queue_claims qc
                JOIN portfolio_frontiers pf
                  ON pf.snapshot_id = qc.snapshot_id
                 AND pf.frontier_fingerprint = qc.frontier_fingerprint
                WHERE qc.state IN ('CLAIMED', 'OUTCOME_UNKNOWN', 'ROUTED')
                """
            ).fetchall()

            candidates = self.connection.execute(
                """
                SELECT
                    pf.snapshot_id,
                    pf.frontier_fingerprint,
                    pf.work_type,
                    pf.collision_keys_json,
                    pf.frontier_json,
                    pf.subject_repository,
                    pf.subject_ref,
                    COALESCE(pf.subject_path, ''),
                    pf.subject_commit,
                    qc.holder,
                    qc.fencing_token,
                    qc.expires_at,
                    qc.state,
                    qc.target_repository,
                    qc.target_ref,
                    qc.target_head,
                    qc.operator_lineage,
                    qc.attempt_generation
                FROM portfolio_frontiers pf
                JOIN portfolio_snapshots ps
                  ON ps.snapshot_id = pf.snapshot_id
                LEFT JOIN portfolio_queue_claims qc
                  ON qc.snapshot_id = pf.snapshot_id
                 AND qc.frontier_fingerprint = pf.frontier_fingerprint
                WHERE ps.registry_digest = ?
                  AND ps.dependency_digest = ?
                  AND ps.worker_registry_digest = ?
                  AND pf.queue_state = 'QUEUED'
                ORDER BY
                    pf.priority_score DESC,
                    pf.snapshot_id DESC,
                    pf.frontier_fingerprint
                """,
                (
                    registry_digest,
                    dependency_digest,
                    worker_registry_digest,
                ),
            ).fetchall()

            seen_fingerprints: set[str] = set()
            for row in candidates:
                snapshot_id = int(row[0])
                fingerprint = str(row[1])
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)

                work_type = str(row[2])
                if work_type not in supported_work_types:
                    continue

                candidate_frontier, _candidate_json = _frontier_from_json(
                    fingerprint=fingerprint,
                    frontier_json=None if row[4] is None else str(row[4]),
                )
                if candidate_frontier.subject.identity() not in current_subjects:
                    continue

                prior_state = None if row[12] is None else str(row[12])
                if prior_state in _NONCLAIMABLE_QUEUE_STATES:
                    continue
                if prior_state == "CLAIMED" and now < float(row[11]):
                    continue

                collision_keys = _canonical_collision_keys(str(row[3]))
                other_reserved_collisions: set[str] = set()
                for (
                    reserved_snapshot,
                    reserved_fingerprint,
                    raw_keys,
                    reserved_frontier_json,
                ) in reservation_rows:
                    if (
                        int(reserved_snapshot) == snapshot_id
                        and str(reserved_fingerprint) == fingerprint
                    ):
                        continue
                    reserved_frontier, _ = _frontier_from_json(
                        fingerprint=str(reserved_fingerprint),
                        frontier_json=(
                            None
                            if reserved_frontier_json is None
                            else str(reserved_frontier_json)
                        ),
                    )
                    if reserved_frontier.subject.identity() not in current_subjects:
                        continue
                    other_reserved_collisions.update(
                        _canonical_collision_keys(str(raw_keys))
                    )
                if collision_keys & other_reserved_collisions:
                    continue

                frontier, target, frontier_json = self._claim_row(
                    fingerprint=fingerprint,
                    frontier_json=None if row[4] is None else str(row[4]),
                    projects=project_tuple,
                )
                if target.repository not in {
                    repo
                    for project in project_tuple
                    if project.id == frontier.project
                    for repo in project.repositories
                }:
                    raise ValueError("execution target escaped project repository scope")
                if row[13] is not None and (
                    str(row[13]) != target.repository
                    or str(row[14]) != target.ref
                ):
                    raise ValueError(
                        "persisted queue target diverges from current registry binding"
                    )

                previous_token = 0 if row[10] is None else int(row[10])
                token = previous_token + 1
                expires_at = now + ttl
                attempt_generation = (
                    1 if row[17] is None else int(row[17])
                )
                expected_lineage = _expected_lineage(
                    snapshot_id,
                    fingerprint,
                    attempt_generation,
                )
                if row[16] is not None and str(row[16]) != expected_lineage:
                    raise ValueError(
                        "persisted queue lineage diverges from durable queue identity"
                    )
                lineage = expected_lineage
                target_head = None if row[15] is None else str(row[15])
                if target_head is not None and _SHA40.fullmatch(target_head) is None:
                    raise ValueError("persisted queue target head is structurally invalid")

                if row[10] is None:
                    self.connection.execute(
                        """
                        INSERT INTO portfolio_queue_claims(
                            snapshot_id, frontier_fingerprint, holder,
                            fencing_token, expires_at, state,
                            target_repository, target_ref, target_head,
                            operator_lineage, attempt_generation,
                            reason, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            snapshot_id,
                            fingerprint,
                            holder,
                            token,
                            expires_at,
                            target.repository,
                            target.ref,
                            target_head,
                            lineage,
                            attempt_generation,
                            now,
                        ),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE portfolio_queue_claims
                        SET holder = ?, fencing_token = ?, expires_at = ?,
                            state = 'CLAIMED', reason = NULL, updated_at = ?
                        WHERE snapshot_id = ? AND frontier_fingerprint = ?
                        """,
                        (
                            holder,
                            token,
                            expires_at,
                            now,
                            snapshot_id,
                            fingerprint,
                        ),
                    )

                self.connection.commit()
                return QueueClaim(
                    snapshot_id=snapshot_id,
                    frontier_fingerprint=fingerprint,
                    frontier=frontier,
                    frontier_json=frontier_json,
                    holder=holder,
                    fencing_token=token,
                    expires_at=expires_at,
                    target_repository=target.repository,
                    target_ref=target.ref,
                    target_head=target_head,
                    operator_lineage=lineage,
                    attempt_generation=attempt_generation,
                )

            self.connection.commit()
            return None
        except BaseException:
            self.connection.rollback()
            raise

    def bind_target_head(
        self,
        claim: QueueClaim,
        *,
        target_head: str,
        now: float,
    ) -> QueueClaim:
        if _SHA40.fullmatch(target_head) is None:
            raise ValueError("queue target head must be lowercase 40-hex")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, expires_at, state, target_head
                FROM portfolio_queue_claims
                WHERE snapshot_id = ? AND frontier_fingerprint = ?
                """,
                (claim.snapshot_id, claim.frontier_fingerprint),
            ).fetchone()
            if row is None:
                raise ValueError("queue claim disappeared")
            if (
                str(row[0]) != claim.holder
                or int(row[1]) != claim.fencing_token
                or str(row[3]) != "CLAIMED"
                or now >= float(row[2])
            ):
                raise ValueError("queue fence no longer authorizes target binding")
            existing = None if row[4] is None else str(row[4])
            if existing is not None and existing != target_head:
                raise ValueError("queue target head is already bound differently")
            if existing is None:
                self.connection.execute(
                    """
                    UPDATE portfolio_queue_claims
                    SET target_head = ?, updated_at = ?
                    WHERE snapshot_id = ? AND frontier_fingerprint = ?
                    """,
                    (
                        target_head,
                        now,
                        claim.snapshot_id,
                        claim.frontier_fingerprint,
                    ),
                )
            self.connection.commit()
            return QueueClaim(
                **{
                    **claim.__dict__,
                    "target_head": existing or target_head,
                }
            )
        except BaseException:
            self.connection.rollback()
            raise

    def finalize(
        self,
        claim: QueueClaim,
        *,
        state: str,
        reason: str,
        now: float,
    ) -> None:
        allowed = _TERMINAL_QUEUE_STATES | {
            "FAILED_RETRYABLE",
            "OUTCOME_UNKNOWN",
            "ROUTED",
        }
        if state not in allowed:
            raise ValueError("unsupported queue finalization state")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT holder, fencing_token, state, expires_at
                FROM portfolio_queue_claims
                WHERE snapshot_id = ? AND frontier_fingerprint = ?
                """,
                (claim.snapshot_id, claim.frontier_fingerprint),
            ).fetchone()
            if row is None:
                raise ValueError("queue claim disappeared")
            if (
                str(row[0]) != claim.holder
                or int(row[1]) != claim.fencing_token
                or str(row[2]) != "CLAIMED"
                or now >= float(row[3])
            ):
                raise ValueError("queue fence no longer authorizes finalization")
            self.connection.execute(
                """
                UPDATE portfolio_queue_claims
                SET state = ?, reason = ?, expires_at = 0, updated_at = ?
                WHERE snapshot_id = ? AND frontier_fingerprint = ?
                """,
                (
                    state,
                    reason,
                    now,
                    claim.snapshot_id,
                    claim.frontier_fingerprint,
                ),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def route_worker_claim(
        self,
        claim: QueueClaim,
        *,
        worker_registry_digest: str,
        packet_sensitivity: str,
        route: ReadOnlyWorkerRoute,
        now: float,
    ) -> WorkerRouteEnvelope:
        if claim.target_head is None:
            raise ValueError("worker route requires an exact bound target head")
        if _SHA256.fullmatch(worker_registry_digest) is None:
            raise ValueError("worker registry digest must be lowercase SHA-256")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT
                    qc.holder,
                    qc.fencing_token,
                    qc.state,
                    qc.expires_at,
                    ps.worker_registry_digest
                FROM portfolio_queue_claims qc
                JOIN portfolio_snapshots ps
                  ON ps.snapshot_id = qc.snapshot_id
                WHERE qc.snapshot_id = ? AND qc.frontier_fingerprint = ?
                """,
                (claim.snapshot_id, claim.frontier_fingerprint),
            ).fetchone()
            if row is None:
                raise ValueError("queue claim disappeared")
            if (
                str(row[0]) != claim.holder
                or int(row[1]) != claim.fencing_token
                or str(row[2]) != "CLAIMED"
                or now >= float(row[3])
            ):
                raise ValueError("queue fence no longer authorizes worker routing")
            if str(row[4]) != worker_registry_digest:
                raise ValueError(
                    "worker route registry digest diverges from scheduling snapshot"
                )

            envelope = enqueue_worker_route_record(
                self.connection,
                snapshot_id=claim.snapshot_id,
                frontier_fingerprint=claim.frontier_fingerprint,
                queue_fencing_token=claim.fencing_token,
                worker_registry_digest=worker_registry_digest,
                packet_sensitivity=packet_sensitivity,
                route=route,
                target_repository=claim.target_repository,
                target_ref=claim.target_ref,
                target_head=claim.target_head,
                frontier_json=claim.frontier_json,
                now=now,
            )
            self.connection.execute(
                """
                UPDATE portfolio_queue_claims
                SET state = 'ROUTED', reason = ?, expires_at = 0, updated_at = ?
                WHERE snapshot_id = ? AND frontier_fingerprint = ?
                """,
                (
                    f"durably routed as {envelope.route_id}",
                    now,
                    claim.snapshot_id,
                    claim.frontier_fingerprint,
                ),
            )
            self.connection.commit()
            return envelope
        except BaseException:
            self.connection.rollback()
            raise

    def routed_subjects_current(
        self,
        *,
        snapshot_id: int,
        frontier_fingerprint_value: str,
        target_currentness_reader: Callable[[str, str], str],
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT
                ps.dependency_digest,
                pf.frontier_json,
                qc.target_repository,
                qc.target_ref,
                qc.target_head
            FROM portfolio_queue_claims qc
            JOIN portfolio_frontiers pf
              ON pf.snapshot_id = qc.snapshot_id
             AND pf.frontier_fingerprint = qc.frontier_fingerprint
            JOIN portfolio_snapshots ps
              ON ps.snapshot_id = qc.snapshot_id
            WHERE qc.snapshot_id = ?
              AND qc.frontier_fingerprint = ?
            """,
            (snapshot_id, frontier_fingerprint_value),
        ).fetchone()
        if row is None:
            raise ValueError("routed currentness subject does not exist")
        dependency_digest = str(row[0])
        frontier, _ = _frontier_from_json(
            fingerprint=frontier_fingerprint_value,
            frontier_json=str(row[1]),
        )
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
        if frontier.subject.identity() not in current_subjects:
            return False
        if (
            frontier.subject.commit is None
            or _SHA40.fullmatch(frontier.subject.commit) is None
        ):
            return False
        observed_provider_head = target_currentness_reader(
            frontier.subject.repository,
            frontier.subject.ref,
        )
        if observed_provider_head != frontier.subject.commit:
            return False

        target_repository = str(row[2])
        target_ref = str(row[3])
        target_head = None if row[4] is None else str(row[4])
        if target_head is None or _SHA40.fullmatch(target_head) is None:
            return False
        observed_target_head = target_currentness_reader(
            target_repository,
            target_ref,
        )
        return observed_target_head == target_head

    def reconcile(
        self,
        *,
        snapshot_id: int,
        frontier_fingerprint_value: str,
        expected_fencing_token: int,
        resolution: str,
        evidence_sha256: str,
        reconciler: str,
        retry_allowed: bool,
        target_currentness_reader: Callable[[str, str], str] | None,
        now: float,
    ) -> QueueReconciliationResult:
        if _SHA256.fullmatch(evidence_sha256) is None:
            raise ValueError("reconciliation evidence must be lowercase SHA-256")
        if not reconciler.strip():
            raise ValueError("reconciler is required")
        resolution_map = {
            "CONFIRM_COMPLETE": "COMPLETE",
            "CONFIRM_SUPERSEDED": "SUPERSEDED",
            "CONFIRM_FAILED_DETERMINISTIC": "FAILED_DETERMINISTIC",
            "RELEASE_RETRY_READ_ONLY": "FAILED_RETRYABLE",
        }
        if resolution not in resolution_map:
            raise ValueError("unsupported queue reconciliation resolution")
        if resolution == "RELEASE_RETRY_READ_ONLY" and not retry_allowed:
            raise ValueError("queue route is not qualified for read-only safe replay")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT state, fencing_token, attempt_generation
                FROM portfolio_queue_claims
                WHERE snapshot_id = ? AND frontier_fingerprint = ?
                """,
                (snapshot_id, frontier_fingerprint_value),
            ).fetchone()
            if row is None:
                raise ValueError("queue reconciliation subject does not exist")
            previous_state = str(row[0])
            if previous_state not in {"OUTCOME_UNKNOWN", "ROUTED"}:
                raise ValueError(
                    "queue reconciliation requires OUTCOME_UNKNOWN or ROUTED state"
                )
            fencing_token = int(row[1])
            if fencing_token != expected_fencing_token:
                raise ValueError("queue reconciliation fencing token mismatch")
            if previous_state == "ROUTED":
                route_row = self.connection.execute(
                    """
                    SELECT
                        route_id, state, receipt_class,
                        receipt_sha256, replay_policy
                    FROM worker_route_outbox
                    WHERE snapshot_id = ?
                      AND frontier_fingerprint = ?
                      AND queue_fencing_token = ?
                    """,
                    (
                        snapshot_id,
                        frontier_fingerprint_value,
                        expected_fencing_token,
                    ),
                ).fetchone()
                if route_row is None:
                    raise ValueError(
                        "routed queue item lacks its durable worker-route envelope"
                    )
                route_state = str(route_row[1])
                if route_state == "RECEIPT_RECORDED":
                    receipt_class = str(route_row[2])
                    compatible_receipts = {
                        "CONFIRM_COMPLETE": {"SUCCEEDED"},
                        "CONFIRM_SUPERSEDED": {"SUPERSEDED"},
                        "CONFIRM_FAILED_DETERMINISTIC": {
                            "FAILED_DETERMINISTIC"
                        },
                        "RELEASE_RETRY_READ_ONLY": {
                            "FAILED_RETRYABLE",
                            "OUTCOME_UNKNOWN",
                        },
                    }
                    if receipt_class not in compatible_receipts[resolution]:
                        raise ValueError(
                            "worker receipt class is incompatible with reconciliation"
                        )
                elif route_state != "OUTCOME_UNKNOWN":
                    raise ValueError(
                        "routed queue item requires a durable worker receipt "
                        "or ambiguous delivery reconciliation state"
                    )

                if resolution == "CONFIRM_COMPLETE":
                    if target_currentness_reader is None:
                        raise ValueError(
                            "routed completion requires independent target currentness"
                        )
                    if not self.routed_subjects_current(
                        snapshot_id=snapshot_id,
                        frontier_fingerprint_value=frontier_fingerprint_value,
                        target_currentness_reader=target_currentness_reader,
                    ):
                        raise ValueError(
                            "routed completion currentness is stale"
                        )
            attempt_generation = int(row[2])
            final_state = resolution_map[resolution]
            next_generation = attempt_generation

            if resolution == "RELEASE_RETRY_READ_ONLY":
                next_generation = attempt_generation + 1
                lineage = _expected_lineage(
                    snapshot_id,
                    frontier_fingerprint_value,
                    next_generation,
                )
                self.connection.execute(
                    """
                    UPDATE portfolio_queue_claims
                    SET holder = NULL, expires_at = 0,
                        state = 'FAILED_RETRYABLE',
                        operator_lineage = ?,
                        attempt_generation = ?,
                        reason = ?, updated_at = ?
                    WHERE snapshot_id = ? AND frontier_fingerprint = ?
                    """,
                    (
                        lineage,
                        next_generation,
                        "explicit reconciliation released read-only retry",
                        now,
                        snapshot_id,
                        frontier_fingerprint_value,
                    ),
                )
                if previous_state == "ROUTED":
                    self.connection.execute(
                        """
                        UPDATE worker_route_outbox
                        SET state = 'RECONCILED_RETRY', updated_at = ?
                        WHERE snapshot_id = ?
                          AND frontier_fingerprint = ?
                          AND queue_fencing_token = ?
                        """,
                        (
                            now,
                            snapshot_id,
                            frontier_fingerprint_value,
                            expected_fencing_token,
                        ),
                    )
            else:
                self.connection.execute(
                    """
                    UPDATE portfolio_queue_claims
                    SET holder = NULL, expires_at = 0,
                        state = ?, reason = ?, updated_at = ?
                    WHERE snapshot_id = ? AND frontier_fingerprint = ?
                    """,
                    (
                        final_state,
                        f"explicit reconciliation: {resolution}",
                        now,
                        snapshot_id,
                        frontier_fingerprint_value,
                    ),
                )
                if previous_state == "ROUTED":
                    self.connection.execute(
                        """
                        UPDATE worker_route_outbox
                        SET state = ?, updated_at = ?
                        WHERE snapshot_id = ?
                          AND frontier_fingerprint = ?
                          AND queue_fencing_token = ?
                        """,
                        (
                            final_state,
                            now,
                            snapshot_id,
                            frontier_fingerprint_value,
                            expected_fencing_token,
                        ),
                    )

            self.connection.execute(
                """
                INSERT INTO portfolio_queue_reconciliations(
                    snapshot_id, frontier_fingerprint,
                    expected_fencing_token, previous_state,
                    resolution, final_state, evidence_sha256,
                    reconciler, reconciled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    frontier_fingerprint_value,
                    expected_fencing_token,
                    previous_state,
                    resolution,
                    final_state,
                    evidence_sha256,
                    reconciler,
                    now,
                ),
            )
            self.connection.commit()
            return QueueReconciliationResult(
                snapshot_id=snapshot_id,
                frontier_fingerprint=frontier_fingerprint_value,
                previous_state=previous_state,
                final_state=final_state,
                fencing_token=fencing_token,
                attempt_generation=next_generation,
                resolution=resolution,
            )
        except BaseException:
            self.connection.rollback()
            raise

    def retry_allowed(
        self,
        *,
        snapshot_id: int,
        frontier_fingerprint_value: str,
        expected_fencing_token: int,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT pf.frontier_json, qc.state
            FROM portfolio_queue_claims qc
            JOIN portfolio_frontiers pf
              ON pf.snapshot_id = qc.snapshot_id
             AND pf.frontier_fingerprint = qc.frontier_fingerprint
            WHERE qc.snapshot_id = ? AND qc.frontier_fingerprint = ?
              AND qc.fencing_token = ?
            """,
            (
                snapshot_id,
                frontier_fingerprint_value,
                expected_fencing_token,
            ),
        ).fetchone()
        if row is None:
            return False
        raw = json.loads(str(row[0]))
        frontier = Frontier.from_mapping(raw)
        if frontier.work_type == "INSPECT":
            worker_route = self.connection.execute(
                """
                SELECT replay_policy
                FROM worker_route_outbox
                WHERE snapshot_id = ?
                  AND frontier_fingerprint = ?
                  AND queue_fencing_token = ?
                """,
                (
                    snapshot_id,
                    frontier_fingerprint_value,
                    expected_fencing_token,
                ),
            ).fetchone()
            if worker_route is None:
                return True
            return ReplayPolicy(str(worker_route[0])) in {
            ReplayPolicy.SAFE,
            ReplayPolicy.RECONCILE_REQUIRED,
        }

        worker_route = self.connection.execute(
            """
            SELECT replay_policy
            FROM worker_route_outbox
            WHERE snapshot_id = ?
              AND frontier_fingerprint = ?
              AND queue_fencing_token = ?
            """,
            (
                snapshot_id,
                frontier_fingerprint_value,
                expected_fencing_token,
            ),
        ).fetchone()
        if worker_route is None:
            return False
        return ReplayPolicy(str(worker_route[0])) in {
            ReplayPolicy.SAFE,
            ReplayPolicy.RECONCILE_REQUIRED,
        }

    def summary(self) -> dict[str, object]:
        rows = self.connection.execute(
            """
            SELECT state, COUNT(*)
            FROM portfolio_queue_claims
            GROUP BY state
            ORDER BY state
            """
        ).fetchall()
        states = {str(state): int(count) for state, count in rows}
        return {
            "claims": sum(states.values()),
            "states": states,
        }


def _read_ref_work(repository: str, ref: str) -> WorkUnit:
    return WorkUnit(
        id="queue-target-read",
        root_frontier_id="queue-target-read",
        parent_work_id=None,
        inputs=(),
        operation="GITHUB",
        required_capabilities=("github.read_ref",),
        collision_keys=(),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 0,
            "retries": 0,
            "backend_jobs": 0,
        },
        expected_outputs=("github-ref-head",),
        completion_criteria=("github-readback-verified",),
        status=WorkUnitStatus.RUNNING,
        payload={
            "github": {
                "operation": GitHubOperation.READ_REF.value,
                "repository": repository,
                "ref": ref,
            }
        },
    )


def _resolve_target_head(
    *,
    repository: str,
    ref: str,
    token: str | None,
    transport: GitHubTransport | None,
) -> str:
    backend = GitHubBackend(
        transport=transport or GitHubRestTransport(token=token),
        route_capabilities=("github.read_ref",),
        grants=(
            TargetAuthorityGrant(
                repository=repository,
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=(ref,),
            ),
        ),
    )
    result = backend.execute(_read_ref_work(repository, ref))
    if not result.succeeded or len(result.outputs) != 1:
        raise RuntimeError("execution target currentness read failed")
    head = result.outputs[0]
    if _SHA40.fullmatch(head) is None:
        raise ValueError("execution target currentness is not an exact commit")
    return head


def _operator_record(
    *,
    state_db: Path,
    claim: QueueClaim,
    registry_digest: str,
):
    if claim.target_head is None:
        return None
    target = ExactSubject(
        repository=claim.target_repository,
        ref=claim.target_ref,
        commit=claim.target_head,
    )
    work = frontier_to_github_inspection_work(
        claim.frontier,
        target,
        registry_digest=registry_digest,
    )
    fingerprint = work_unit_fingerprint(work)
    store = SqliteRecursiveWorkStore(state_db)
    try:
        record = store.get(claim.operator_lineage, fingerprint)
        if record is not None:
            return record
        unexpected = store.connection.execute(
            """
            SELECT work_fingerprint
            FROM recursive_work_state
            WHERE lineage_id = ?
            LIMIT 1
            """,
            (claim.operator_lineage,),
        ).fetchone()
        if unexpected is not None:
            raise ValueError(
                "operator lineage contains an unexpected durable work identity"
            )
        return None
    finally:
        store.close()


def _queue_state_for_operator(status: WorkUnitStatus) -> str:
    if status is WorkUnitStatus.COMPLETE:
        return "COMPLETE"
    if status is WorkUnitStatus.FAILED_DETERMINISTIC:
        return "FAILED_DETERMINISTIC"
    if status is WorkUnitStatus.SUPERSEDED:
        return "SUPERSEDED"
    return "OUTCOME_UNKNOWN"


def consume_next_queued_read_only_work(
    *,
    projects: Iterable[ProjectDefinition],
    workers: Iterable[WorkerDefinition],
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    state_db: Path,
    holder: str,
    lease_ttl: float,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> QueueConsumptionResult:
    project_tuple = tuple(projects)
    worker_tuple = tuple(workers)
    effective_transport = transport or GitHubRestTransport(token=token)
    store = SqliteQueueStore(Path(state_db))
    try:
        claim = store.claim_next(
            projects=project_tuple,
            registry_digest=registry_digest,
            dependency_digest=dependency_digest,
            worker_registry_digest=worker_registry_digest,
            holder=holder,
            now=clock(),
            ttl=lease_ttl,
        )
        if claim is None:
            return QueueConsumptionResult(
                claimed=False,
                queue_state="NO_WORK",
                reason=(
                    "no READY queued read-only frontier for the current configuration"
                ),
            )

        if claim.target_head is None:
            head = _resolve_target_head(
                repository=claim.target_repository,
                ref=claim.target_ref,
                token=token,
                transport=effective_transport,
            )
            claim = store.bind_target_head(
                claim,
                target_head=head,
                now=clock(),
            )

        project = _project_for_frontier(project_tuple, claim.frontier)
        try:
            worker_route = resolve_read_only_worker_route(
                project=project,
                frontier=claim.frontier,
                workers=worker_tuple,
            )
        except ValueError:
            store.finalize(
                claim,
                state="FAILED_RETRYABLE",
                reason="read-only worker route is not currently authorized",
                now=clock(),
            )
            raise

        if worker_route is not None:
            envelope = store.route_worker_claim(
                claim,
                worker_registry_digest=worker_registry_digest,
                packet_sensitivity=_packet_sensitivity(
                    project_tuple,
                    claim.frontier,
                ),
                route=worker_route,
                now=clock(),
            )
            return QueueConsumptionResult(
                claimed=True,
                queue_state="ROUTED",
                snapshot_id=claim.snapshot_id,
                frontier_fingerprint=claim.frontier_fingerprint,
                fencing_token=claim.fencing_token,
                route_id=envelope.route_id,
                reason="durable read-only worker route created",
            )

        if claim.frontier.work_type != "INSPECT":
            store.finalize(
                claim,
                state="FAILED_DETERMINISTIC",
                reason="non-INSPECT work lacks an explicit worker route",
                now=clock(),
            )
            raise ValueError("non-INSPECT work lacks an explicit worker route")

        try:
            existing = _operator_record(
                state_db=Path(state_db),
                claim=claim,
                registry_digest=registry_digest,
            )
            if existing is not None:
                state = _queue_state_for_operator(existing.work.status)
                if state == "OUTCOME_UNKNOWN":
                    reason = (
                        "durable operator state already exists but is nonterminal; "
                        "backend re-execution was not attempted"
                    )
                else:
                    reason = (
                        "queue reconciled from existing durable operator terminal state"
                    )
                store.finalize(
                    claim,
                    state=state,
                    reason=reason,
                    now=clock(),
                )
                return QueueConsumptionResult(
                    claimed=True,
                    queue_state=state,
                    snapshot_id=claim.snapshot_id,
                    frontier_fingerprint=claim.frontier_fingerprint,
                    fencing_token=claim.fencing_token,
                    operator_status=existing.work.status.value,
                    reason=reason,
                )

            target = ExactSubject(
                repository=claim.target_repository,
                ref=claim.target_ref,
                commit=claim.target_head,
            )
            result: InspectionRunResult = run_durable_github_read_inspection(
                frontier=claim.frontier,
                target_subject=target,
                state_db=Path(state_db),
                lineage_id=claim.operator_lineage,
                holder=holder,
                lease_ttl=lease_ttl,
                registry_digest=registry_digest,
                authorized_target_repositories=(
                    claim.target_repository,
                ),
                authorized_provider_repositories=tuple(
                    repository
                    for project_item in project_tuple
                    for repository in project_item.repositories
                ),
                token=token,
                transport=effective_transport,
                clock=clock,
            )
            queue_state = _queue_state_for_operator(result.status)
            store.finalize(
                claim,
                state=queue_state,
                reason=result.reason,
                now=clock(),
            )
            return QueueConsumptionResult(
                claimed=True,
                queue_state=queue_state,
                snapshot_id=claim.snapshot_id,
                frontier_fingerprint=claim.frontier_fingerprint,
                fencing_token=claim.fencing_token,
                operator_status=result.status.value,
                reason=result.reason,
            )
        except Exception:
            existing = _operator_record(
                state_db=Path(state_db),
                claim=claim,
                registry_digest=registry_digest,
            )
            if existing is None:
                recovery_state = "FAILED_RETRYABLE"
                recovery_reason = (
                    "queue execution failed before durable operator state existed"
                )
            elif existing.work.status in {
                WorkUnitStatus.COMPLETE,
                WorkUnitStatus.FAILED_DETERMINISTIC,
                WorkUnitStatus.SUPERSEDED,
            }:
                recovery_state = _queue_state_for_operator(existing.work.status)
                recovery_reason = (
                    "queue recovered terminal operator state after execution error"
                )
            else:
                recovery_state = "OUTCOME_UNKNOWN"
                recovery_reason = (
                    "durable operator state is nonterminal after execution error; "
                    "backend re-execution requires reconciliation"
                )
            try:
                store.finalize(
                    claim,
                    state=recovery_state,
                    reason=recovery_reason,
                    now=clock(),
                )
            except ValueError as finalize_error:
                if "queue fence no longer authorizes finalization" not in str(
                    finalize_error
                ):
                    raise
            raise
    finally:
        store.close()


def consume_next_queued_inspection(
    *,
    projects: Iterable[ProjectDefinition],
    registry_digest: str,
    dependency_digest: str,
    state_db: Path,
    holder: str,
    lease_ttl: float,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> QueueConsumptionResult:
    return consume_next_queued_read_only_work(
        projects=projects,
        workers=(),
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        worker_registry_digest="0" * 64,
        state_db=state_db,
        holder=holder,
        lease_ttl=lease_ttl,
        token=token,
        transport=transport,
        clock=clock,
    )


def reconcile_queue_item(
    *,
    state_db: Path,
    snapshot_id: int,
    frontier_fingerprint_value: str,
    expected_fencing_token: int,
    resolution: str,
    evidence_sha256: str,
    reconciler: str,
    token: str | None = None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> QueueReconciliationResult:
    store = SqliteQueueStore(Path(state_db))
    try:
        retry_allowed = store.retry_allowed(
            snapshot_id=snapshot_id,
            frontier_fingerprint_value=frontier_fingerprint_value,
            expected_fencing_token=expected_fencing_token,
        )
        return store.reconcile(
            snapshot_id=snapshot_id,
            frontier_fingerprint_value=frontier_fingerprint_value,
            expected_fencing_token=expected_fencing_token,
            resolution=resolution,
            evidence_sha256=evidence_sha256,
            reconciler=reconciler,
            retry_allowed=retry_allowed,
            target_currentness_reader=(
                (transport or GitHubRestTransport(token=token)).read_ref
                if resolution == "CONFIRM_COMPLETE"
                else None
            ),
            now=clock(),
        )
    finally:
        store.close()


def summarize_queue_state(state_db: Path) -> dict[str, object]:
    state_db = Path(state_db)
    if not state_db.exists():
        return {"claims": 0, "states": {}}
    store = SqliteQueueStore(state_db)
    try:
        return store.summary()
    finally:
        store.close()
