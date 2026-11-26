from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Iterable

from .currentness import observation_locus, subject_changed
from .dedup import deduplicate_frontiers, frontier_fingerprint
from .frontier import derive_frontiers
from .github_backend import (
    GitHubBackend,
    GitHubOperation,
    GitHubRestTransport,
    GitHubTransport,
    TargetAuthorityGrant,
)
from .models import (
    DependencyEdge,
    EvidenceClass,
    ExactSubject,
    Frontier,
    FrontierStatus,
    Observation,
    ProjectDefinition,
    WorkerDefinition,
)
from .prioritize import rank_frontiers
from .propagate import derive_invalidations
from .work_units import WorkUnit, WorkUnitStatus
from .worker_routing import worker_route_ready


_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class PortfolioCycleResult:
    snapshot_id: int
    snapshot_digest: str
    baseline: bool
    observation_count: int
    changed_count: int
    frontier_count: int
    ready_count: int
    blocked_count: int
    queued_count: int


def _empty_summary() -> dict[str, object]:
    return {
        "snapshots": 0,
        "latest_snapshot_id": None,
        "latest_snapshot_digest": None,
        "baseline": None,
        "observations": 0,
        "changed": 0,
        "frontiers": 0,
        "ready": 0,
        "blocked": 0,
        "queued_total": 0,
    }


def _frontier_mapping(frontier: Frontier) -> dict[str, object]:
    return {
        "id": frontier.id,
        "project": frontier.project,
        "subject": {
            "repository": frontier.subject.repository,
            "ref": frontier.subject.ref,
            "commit": frontier.subject.commit,
            "path": frontier.subject.path,
            "digest": frontier.subject.digest,
        },
        "work_type": frontier.work_type,
        "reason": frontier.reason,
        "dependencies": list(frontier.dependencies),
        "required_capabilities": list(frontier.required_capabilities),
        "collision_keys": list(frontier.collision_keys),
        "cost_class": frontier.cost_class.value,
        "priority_inputs": dict(frontier.priority_inputs),
        "status": frontier.status.value,
    }


def _ensure_frontier_json_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(portfolio_frontiers)")
    }
    if "frontier_json" not in columns:
        connection.execute(
            "ALTER TABLE portfolio_frontiers ADD COLUMN frontier_json TEXT"
        )


class SqlitePortfolioStore:
    """Durable portfolio-currentness snapshots and scheduler decisions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                registry_digest TEXT NOT NULL,
                dependency_digest TEXT NOT NULL,
                worker_registry_digest TEXT NOT NULL DEFAULT 'legacy-unbound',
                snapshot_digest TEXT NOT NULL,
                observed_at REAL NOT NULL,
                baseline INTEGER NOT NULL CHECK (baseline IN (0, 1)),
                observation_count INTEGER NOT NULL,
                changed_count INTEGER NOT NULL,
                frontier_count INTEGER NOT NULL,
                ready_count INTEGER NOT NULL,
                blocked_count INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshot_config
                ON portfolio_snapshots(registry_digest, dependency_digest, snapshot_id);

            CREATE TABLE IF NOT EXISTS portfolio_observations (
                snapshot_id INTEGER NOT NULL,
                target TEXT NOT NULL,
                repository TEXT NOT NULL,
                ref TEXT NOT NULL,
                path TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                digest TEXT,
                observed_value TEXT NOT NULL,
                evidence_class TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                observer TEXT NOT NULL,
                PRIMARY KEY (snapshot_id, target, repository, ref, path),
                FOREIGN KEY (snapshot_id)
                    REFERENCES portfolio_snapshots(snapshot_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS portfolio_frontiers (
                snapshot_id INTEGER NOT NULL,
                frontier_fingerprint TEXT NOT NULL,
                frontier_id TEXT NOT NULL,
                project TEXT NOT NULL,
                work_type TEXT NOT NULL,
                status TEXT NOT NULL,
                priority_score INTEGER NOT NULL,
                queue_state TEXT NOT NULL CHECK (queue_state IN ('QUEUED', 'BLOCKED')),
                subject_repository TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                subject_commit TEXT,
                subject_path TEXT,
                collision_keys_json TEXT NOT NULL,
                required_capabilities_json TEXT NOT NULL,
                frontier_json TEXT,
                PRIMARY KEY (snapshot_id, frontier_fingerprint),
                FOREIGN KEY (snapshot_id)
                    REFERENCES portfolio_snapshots(snapshot_id)
                    ON DELETE CASCADE
            );
            """
        )
        snapshot_columns = {
            str(row[1])
            for row in self.connection.execute(
                "PRAGMA table_info(portfolio_snapshots)"
            )
        }
        if "worker_registry_digest" not in snapshot_columns:
            self.connection.execute(
                """
                ALTER TABLE portfolio_snapshots
                ADD COLUMN worker_registry_digest TEXT
                NOT NULL DEFAULT 'legacy-unbound'
                """
            )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshot_config_v2
            ON portfolio_snapshots(
                registry_digest,
                dependency_digest,
                worker_registry_digest,
                snapshot_id
            )
            """
        )
        _ensure_frontier_json_column(self.connection)

    def close(self) -> None:
        self.connection.close()

    def latest_compatible_snapshot_id(
        self,
        *,
        registry_digest: str,
        dependency_digest: str,
        worker_registry_digest: str,
    ) -> int | None:
        row = self.connection.execute(
            """
            SELECT snapshot_id
            FROM portfolio_snapshots
            WHERE registry_digest = ?
              AND dependency_digest = ?
              AND worker_registry_digest = ?
            ORDER BY snapshot_id DESC
            LIMIT 1
            """,
            (registry_digest, dependency_digest, worker_registry_digest),
        ).fetchone()
        return None if row is None else int(row[0])

    def latest_snapshot_id_for_dependency(
        self,
        *,
        dependency_digest: str,
    ) -> int | None:
        row = self.connection.execute(
            """
            SELECT snapshot_id
            FROM portfolio_snapshots
            WHERE dependency_digest = ?
            ORDER BY snapshot_id DESC
            LIMIT 1
            """,
            (dependency_digest,),
        ).fetchone()
        return None if row is None else int(row[0])

    def load_unresolved_frontiers_for_dependency(
        self,
        *,
        dependency_digest: str,
    ) -> tuple[Frontier, ...]:
        has_claim_table = self.connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'portfolio_queue_claims'
            """
        ).fetchone() is not None
        if has_claim_table:
            rows = self.connection.execute(
                """
                SELECT
                    pf.frontier_fingerprint,
                    pf.frontier_json,
                    qc.state
                FROM portfolio_frontiers pf
                JOIN portfolio_snapshots ps
                  ON ps.snapshot_id = pf.snapshot_id
                LEFT JOIN portfolio_queue_claims qc
                  ON qc.snapshot_id = pf.snapshot_id
                 AND qc.frontier_fingerprint = pf.frontier_fingerprint
                WHERE ps.dependency_digest = ?
                ORDER BY pf.snapshot_id DESC, pf.frontier_fingerprint
                """,
                (dependency_digest,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT
                    pf.frontier_fingerprint,
                    pf.frontier_json,
                    NULL
                FROM portfolio_frontiers pf
                JOIN portfolio_snapshots ps
                  ON ps.snapshot_id = pf.snapshot_id
                WHERE ps.dependency_digest = ?
                ORDER BY pf.snapshot_id DESC, pf.frontier_fingerprint
                """,
                (dependency_digest,),
            ).fetchall()

        seen: set[str] = set()
        result: list[Frontier] = []
        for fingerprint, frontier_json, queue_state in rows:
            fingerprint_text = str(fingerprint)
            if fingerprint_text in seen:
                continue
            seen.add(fingerprint_text)
            if queue_state is not None and str(queue_state) not in {
                "FAILED_RETRYABLE",
            }:
                continue
            if frontier_json is None:
                continue
            raw = json.loads(str(frontier_json))
            if not isinstance(raw, dict):
                raise ValueError("stored frontier payload is structurally invalid")
            frontier = Frontier.from_mapping(raw)
            if frontier_fingerprint(frontier) != fingerprint_text:
                raise ValueError(
                    "stored frontier fingerprint does not match durable payload"
                )
            result.append(frontier)
        return tuple(result)

    def load_observations(self, snapshot_id: int) -> tuple[Observation, ...]:
        rows = self.connection.execute(
            """
            SELECT
                target, repository, ref, path, commit_sha, digest,
                observed_value, evidence_class, observed_at, observer
            FROM portfolio_observations
            WHERE snapshot_id = ?
            ORDER BY target, repository, ref, path
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(
            Observation(
                target=str(row[0]),
                evidence_class=EvidenceClass(str(row[7])),
                subject=ExactSubject(
                    repository=str(row[1]),
                    ref=str(row[2]),
                    commit=str(row[4]),
                    path=str(row[3]) or None,
                    digest=str(row[5]) if row[5] is not None else None,
                ),
                observed_value=str(row[6]),
                observed_at=str(row[8]),
                observer=str(row[9]),
            )
            for row in rows
        )

    def commit_cycle(
        self,
        *,
        registry_digest: str,
        dependency_digest: str,
        worker_registry_digest: str,
        snapshot_digest: str,
        observed_at: float,
        baseline: bool,
        observations: tuple[Observation, ...],
        changed_count: int,
        ranked_frontiers,
        expected_previous_snapshot_id: int | None,
        expected_previous_dependency_snapshot_id: int | None,
    ) -> int:
        ready_count = sum(
            1
            for decision in ranked_frontiers
            if decision.frontier.status is FrontierStatus.READY
        )
        frontier_count = len(ranked_frontiers)
        blocked_count = frontier_count - ready_count

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
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
            actual_previous = None if row is None else int(row[0])
            if actual_previous != expected_previous_snapshot_id:
                raise RuntimeError(
                    "portfolio configuration currentness changed concurrently; "
                    "retry from fresh state"
                )

            dependency_row = self.connection.execute(
                """
                SELECT snapshot_id
                FROM portfolio_snapshots
                WHERE dependency_digest = ?
                ORDER BY snapshot_id DESC
                LIMIT 1
                """,
                (dependency_digest,),
            ).fetchone()
            actual_dependency_previous = (
                None if dependency_row is None else int(dependency_row[0])
            )
            if (
                actual_dependency_previous
                != expected_previous_dependency_snapshot_id
            ):
                raise RuntimeError(
                    "portfolio topology currentness changed concurrently; "
                    "retry from fresh state"
                )

            cursor = self.connection.execute(
                """
                INSERT INTO portfolio_snapshots(
                    registry_digest, dependency_digest,
                    worker_registry_digest, snapshot_digest,
                    observed_at, baseline, observation_count, changed_count,
                    frontier_count, ready_count, blocked_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    registry_digest,
                    dependency_digest,
                    worker_registry_digest,
                    snapshot_digest,
                    observed_at,
                    int(baseline),
                    len(observations),
                    changed_count,
                    frontier_count,
                    ready_count,
                    blocked_count,
                ),
            )
            snapshot_id = int(cursor.lastrowid)

            self.connection.executemany(
                """
                INSERT INTO portfolio_observations(
                    snapshot_id, target, repository, ref, path, commit_sha,
                    digest, observed_value, evidence_class, observed_at, observer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        observation.target,
                        observation.subject.repository,
                        observation.subject.ref,
                        observation.subject.path or "",
                        observation.subject.commit,
                        observation.subject.digest,
                        observation.observed_value,
                        observation.evidence_class.value,
                        observation.observed_at,
                        observation.observer,
                    )
                    for observation in observations
                ],
            )

            self.connection.executemany(
                """
                INSERT INTO portfolio_frontiers(
                    snapshot_id, frontier_fingerprint, frontier_id, project,
                    work_type, status, priority_score, queue_state,
                    subject_repository, subject_ref, subject_commit, subject_path,
                    collision_keys_json, required_capabilities_json,
                    frontier_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        frontier_fingerprint(decision.frontier),
                        decision.frontier.id,
                        decision.frontier.project,
                        decision.frontier.work_type,
                        decision.frontier.status.value,
                        decision.score,
                        (
                            "QUEUED"
                            if decision.frontier.status is FrontierStatus.READY
                            else "BLOCKED"
                        ),
                        decision.frontier.subject.repository,
                        decision.frontier.subject.ref,
                        decision.frontier.subject.commit,
                        decision.frontier.subject.path,
                        json.dumps(
                            list(decision.frontier.collision_keys),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            list(decision.frontier.required_capabilities),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            _frontier_mapping(decision.frontier),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                    for decision in ranked_frontiers
                ],
            )
            self.connection.commit()
            return snapshot_id
        except Exception:
            self.connection.rollback()
            raise

    def summary(self) -> dict[str, object]:
        latest = self.connection.execute(
            """
            SELECT
                snapshot_id, snapshot_digest, baseline, observation_count,
                changed_count, frontier_count, ready_count, blocked_count,
                dependency_digest
            FROM portfolio_snapshots
            ORDER BY snapshot_id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            return _empty_summary()

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
        unresolved = self.load_unresolved_frontiers_for_dependency(
            dependency_digest=str(latest[8])
        )
        queued = sum(
            1
            for frontier in unresolved
            if frontier.status is FrontierStatus.READY
            and frontier.subject.identity() in current_subjects
        )

        snapshots = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM portfolio_snapshots"
            ).fetchone()[0]
        )
        return {
            "snapshots": snapshots,
            "latest_snapshot_id": latest_snapshot_id,
            "latest_snapshot_digest": str(latest[1]),
            "baseline": bool(latest[2]),
            "observations": int(latest[3]),
            "changed": int(latest[4]),
            "frontiers": int(latest[5]),
            "ready": int(latest[6]),
            "blocked": int(latest[7]),
            "queued_total": queued,
        }


def _validate_digest(value: str, *, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _project_index(
    projects: Iterable[ProjectDefinition],
) -> dict[str, ProjectDefinition]:
    result: dict[str, ProjectDefinition] = {}
    for project in projects:
        if project.id in result:
            raise ValueError(f"duplicate project id: {project.id}")
        result[project.id] = project
    return result


def _validate_dependency_scope(
    projects: tuple[ProjectDefinition, ...],
    dependencies: tuple[DependencyEdge, ...],
) -> None:
    by_id = _project_index(projects)
    for edge in dependencies:
        provider = by_id.get(edge.provider)
        consumer = by_id.get(edge.consumer)
        if provider is None or consumer is None:
            raise ValueError(
                "dependency references a project absent from the current registry"
            )
        if edge.selector.repository not in provider.repositories:
            raise ValueError(
                "dependency selector repository is outside its provider project scope"
            )
        if edge.selector.ref is None or not edge.selector.ref.strip():
            raise ValueError("portfolio currentness requires an exact dependency ref")


def _reevaluate_frontier_readiness(
    frontiers: tuple[Frontier, ...],
    projects: tuple[ProjectDefinition, ...],
    workers: tuple[WorkerDefinition, ...],
) -> tuple[Frontier, ...]:
    by_id = {project.id: project for project in projects}
    result: list[Frontier] = []
    for frontier in frontiers:
        if frontier.status is FrontierStatus.WAITING_DEPENDENCY:
            result.append(frontier)
            continue

        project = by_id.get(frontier.project)
        capabilities_ok = (
            project is not None
            and set(frontier.required_capabilities).issubset(
                set(project.capabilities)
            )
        )
        scheduling_ok = (
            project is not None
            and project.scheduling_state.schedulable
        )
        route_ok = (
            project is not None
            and worker_route_ready(
                project=project,
                frontier=frontier,
                workers=workers,
            )
        )

        if not scheduling_ok:
            status = FrontierStatus.WAITING_SCHEDULING
        elif not capabilities_ok or not route_ok:
            status = FrontierStatus.WAITING_AUTHORITY
        else:
            status = FrontierStatus.READY

        priority_inputs = dict(frontier.priority_inputs)
        priority_inputs["authority_available"] = int(
            capabilities_ok and route_ok
        )
        priority_inputs["scheduling_eligible"] = int(scheduling_ok)
        priority_inputs["executable_now"] = int(
            status is FrontierStatus.READY
        )
        result.append(
            replace(
                frontier,
                status=status,
                priority_inputs=priority_inputs,
            )
        )
    return tuple(result)


def _frontier_is_current(
    frontier: Frontier,
    current_observations: tuple[Observation, ...],
) -> bool:
    current_identities = {
        observation.subject.identity()
        for observation in current_observations
    }
    return frontier.subject.identity() in current_identities


def _private_collision_key(key: str, secret_hex: str) -> str:
    if len(secret_hex) != 64 or any(
        character not in "0123456789abcdef" for character in secret_hex
    ):
        raise ValueError("private collision key must be 64 lowercase hex characters")
    digest = hmac.new(
        bytes.fromhex(secret_hex),
        key.encode("utf-8"),
        digestmod="sha256",
    ).hexdigest()
    return f"private:{digest}"


def _read_ref_work(repository: str, ref: str) -> WorkUnit:
    identity = hashlib.sha256(
        f"{repository}@{ref}".encode("utf-8")
    ).hexdigest()[:16]
    return WorkUnit(
        id=f"portfolio-read-{identity}",
        root_frontier_id=f"portfolio-currentness-{identity}",
        parent_work_id=None,
        inputs=(),
        operation="GITHUB",
        required_capabilities=("read",),
        collision_keys=(),
        recursion_depth=0,
        budget_allocation={"active": 1, "backend_jobs": 1},
        expected_outputs=("github-ref",),
        completion_criteria=("readback",),
        status=WorkUnitStatus.PENDING,
        payload={
            "github": {
                "operation": GitHubOperation.READ_REF.value,
                "repository": repository,
                "ref": ref,
            }
        },
    )


def _collect_observations(
    *,
    projects: tuple[ProjectDefinition, ...],
    dependencies: tuple[DependencyEdge, ...],
    transport: GitHubTransport,
    observed_at: float,
) -> tuple[Observation, ...]:
    _validate_dependency_scope(projects, dependencies)
    scopes = sorted(
        {
            (edge.selector.repository, str(edge.selector.ref))
            for edge in dependencies
        }
    )
    backend = GitHubBackend(
        transport=transport,
        route_capabilities=("github.read_ref",),
        grants=tuple(
            TargetAuthorityGrant(
                repository=repository,
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=(ref,),
            )
            for repository, ref in scopes
        ),
    )

    head_cache: dict[tuple[str, str], str] = {}
    for repository, ref in scopes:
        result = backend.execute(_read_ref_work(repository, ref))
        if not result.succeeded or len(result.outputs) != 1:
            raise RuntimeError("portfolio currentness read failed")
        head = result.outputs[0]
        if _SHA40.fullmatch(head) is None:
            raise ValueError("github currentness read returned a non-exact commit")
        head_cache[(repository, ref)] = head

    observations: dict[tuple[str, str, str, str | None], Observation] = {}
    observed_at_iso = datetime.fromtimestamp(
        observed_at,
        tz=timezone.utc,
    ).isoformat()
    for edge in dependencies:
        ref = str(edge.selector.ref)
        head = head_cache[(edge.selector.repository, ref)]
        subject = ExactSubject(
            repository=edge.selector.repository,
            ref=ref,
            commit=head,
            path=edge.selector.path_prefix,
        )
        locus = (
            edge.provider,
            subject.repository,
            subject.ref,
            subject.path,
        )
        observations[locus] = Observation(
            target=edge.provider,
            evidence_class=EvidenceClass.AUTHORITATIVE,
            subject=subject,
            observed_value=head,
            observed_at=observed_at_iso,
            observer="project-runner:portfolio-currentness-v1",
        )

    return tuple(observations[key] for key in sorted(observations))


def _snapshot_digest(
    *,
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    observations: tuple[Observation, ...],
) -> str:
    payload = {
        "registry_digest": registry_digest,
        "dependency_digest": dependency_digest,
        "worker_registry_digest": worker_registry_digest,
        "observations": [
            {
                "target": item.target,
                "subject": item.subject.identity(),
                "observed_value": item.observed_value,
            }
            for item in observations
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def collect_and_schedule_portfolio(
    *,
    projects: Iterable[ProjectDefinition],
    dependencies: Iterable[DependencyEdge],
    workers: Iterable[WorkerDefinition] = (),
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str = "0000000000000000000000000000000000000000000000000000000000000000",
    state_db: Path,
    token: str | None,
    transport: GitHubTransport | None = None,
    private_collision_key: str | None = None,
    clock: Callable[[], float] = time.time,
) -> PortfolioCycleResult:
    """Collect one read-only snapshot and atomically persist scheduler decisions.

    The first cycle for an exact registry+dependency digest pair establishes a
    baseline and schedules nothing. Later compatible cycles compare against that
    exact predecessor. A snapshot and its scheduler decisions commit together.
    """
    _validate_digest(registry_digest, label="project registry digest")
    _validate_digest(dependency_digest, label="dependency registry digest")
    _validate_digest(
        worker_registry_digest,
        label="worker registry digest",
    )
    project_tuple = tuple(projects)
    dependency_tuple = tuple(dependencies)
    worker_tuple = tuple(workers)
    _validate_dependency_scope(project_tuple, dependency_tuple)

    now = float(clock())
    current = _collect_observations(
        projects=project_tuple,
        dependencies=dependency_tuple,
        transport=transport or GitHubRestTransport(token=token),
        observed_at=now,
    )
    digest = _snapshot_digest(
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        worker_registry_digest=worker_registry_digest,
        observations=current,
    )

    store = SqlitePortfolioStore(Path(state_db))
    try:
        previous_config_snapshot_id = store.latest_compatible_snapshot_id(
            registry_digest=registry_digest,
            dependency_digest=dependency_digest,
            worker_registry_digest=worker_registry_digest,
        )
        previous_dependency_snapshot_id = (
            store.latest_snapshot_id_for_dependency(
                dependency_digest=dependency_digest,
            )
        )
        baseline = previous_dependency_snapshot_id is None
        if baseline:
            changed_count = 0
            ranked = ()
        else:
            previous = store.load_observations(
                previous_dependency_snapshot_id
            )
            previous_by_locus = {
                observation_locus(item): item
                for item in previous
            }
            changed_count = sum(
                1
                for item in current
                if subject_changed(
                    previous_by_locus.get(observation_locus(item)),
                    item,
                )
            )
            invalidations = derive_invalidations(
                previous,
                current,
                dependency_tuple,
            )
            capability_lookup = {
                project.id: set(project.capabilities)
                for project in project_tuple
            }
            scheduling_lookup = {
                project.id: project.scheduling_state.schedulable
                for project in project_tuple
            }
            derived = derive_frontiers(
                invalidations,
                capability_lookup=capability_lookup,
                scheduling_lookup=scheduling_lookup,
            )

            current_dependency_ids = {
                dependency.id for dependency in dependency_tuple
            }
            current_project_ids = {
                project.id for project in project_tuple
            }
            carried = tuple(
                frontier
                for frontier in store.load_unresolved_frontiers_for_dependency(
                    dependency_digest=dependency_digest
                )
                if frontier.project in current_project_ids
                and _frontier_is_current(frontier, current)
                and set(frontier.dependencies).issubset(
                    current_dependency_ids
                )
            )
            frontiers = _reevaluate_frontier_readiness(
                tuple(derived) + carried,
                project_tuple,
                worker_tuple,
            )
            if private_collision_key is not None:
                frontiers = tuple(
                    replace(
                        frontier,
                        collision_keys=(
                            _private_collision_key(
                                f"project:{frontier.project}",
                                private_collision_key,
                            ),
                        ),
                    )
                    for frontier in frontiers
                )
            ranked = rank_frontiers(
                deduplicate_frontiers(frontiers)
            )

        snapshot_id = store.commit_cycle(
            registry_digest=registry_digest,
            dependency_digest=dependency_digest,
            worker_registry_digest=worker_registry_digest,
            snapshot_digest=digest,
            observed_at=now,
            baseline=baseline,
            observations=current,
            changed_count=changed_count,
            ranked_frontiers=ranked,
            expected_previous_snapshot_id=previous_config_snapshot_id,
            expected_previous_dependency_snapshot_id=(
                previous_dependency_snapshot_id
            ),
        )
        ready_count = sum(
            1
            for decision in ranked
            if decision.frontier.status is FrontierStatus.READY
        )
        frontier_count = len(ranked)
        return PortfolioCycleResult(
            snapshot_id=snapshot_id,
            snapshot_digest=digest,
            baseline=baseline,
            observation_count=len(current),
            changed_count=changed_count,
            frontier_count=frontier_count,
            ready_count=ready_count,
            blocked_count=frontier_count - ready_count,
            queued_count=ready_count,
        )
    finally:
        store.close()


def summarize_portfolio_state(state_db: Path) -> dict[str, object]:
    state_db = Path(state_db)
    if not state_db.exists():
        return _empty_summary()
    store = SqlitePortfolioStore(state_db)
    try:
        return store.summary()
    finally:
        store.close()
