from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable

from runner.github_backend import GitHubTransport
from runner.models import DependencyEdge, ProjectDefinition, WorkerDefinition
from runner.portfolio import PortfolioCycleResult, collect_and_schedule_portfolio
from runner.queue_consumer import (
    QueueConsumptionResult,
    consume_next_queued_read_only_work,
    summarize_queue_state,
)

from .models import ExecutionNode


@dataclass(frozen=True)
class PortalLaneResult:
    node_id: str
    slot: int
    claimed: bool
    queue_state: str
    snapshot_id: int | None
    frontier_fingerprint: str | None
    fencing_token: int | None
    operator_status: str | None
    route_id: str | None
    reason: str


@dataclass(frozen=True)
class PortalCycleResult:
    run_id: str
    cycle_number: int
    cycle: PortfolioCycleResult
    lanes: tuple[PortalLaneResult, ...]
    queue_summary: dict[str, object]
    progress_made: bool


@dataclass(frozen=True)
class PortalRunResult:
    run_id: str
    cycles: tuple[PortalCycleResult, ...]
    stop_reason: str
    idle_cycles: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_runs (
    run_id TEXT PRIMARY KEY,
    config_digest TEXT NOT NULL,
    holder TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('READY', 'RUNNING', 'STOPPED', 'FAILED')
    ),
    cycle_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_cycles (
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('STARTED', 'COMPLETE', 'FAILED')
    ),
    snapshot_id INTEGER,
    snapshot_digest TEXT,
    baseline INTEGER,
    ready_count INTEGER,
    blocked_count INTEGER,
    lane_slots INTEGER NOT NULL,
    progress_made INTEGER,
    started_at REAL NOT NULL,
    completed_at REAL,
    failure_reason TEXT,
    PRIMARY KEY (run_id, cycle_number),
    FOREIGN KEY (run_id) REFERENCES portal_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portal_lane_events (
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    claimed INTEGER NOT NULL CHECK (claimed IN (0, 1)),
    queue_state TEXT NOT NULL,
    snapshot_id INTEGER,
    frontier_fingerprint TEXT,
    fencing_token INTEGER,
    operator_status TEXT,
    route_id TEXT,
    reason TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (run_id, cycle_number, node_id, slot),
    FOREIGN KEY (run_id, cycle_number)
        REFERENCES portal_cycles(run_id, cycle_number)
        ON DELETE CASCADE
);
"""


def _canonical_digest(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run_config_digest(
    *,
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    nodes: tuple[ExecutionNode, ...],
    max_parallel: int,
) -> str:
    return _canonical_digest(
        {
            "schema": "PORTAL_RUN_CONFIG_V1",
            "registry_digest": registry_digest,
            "dependency_digest": dependency_digest,
            "worker_registry_digest": worker_registry_digest,
            "max_parallel": max_parallel,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "max_parallel": node.max_parallel,
                    "allowed_lanes": list(node.allowed_lanes),
                    "enabled": node.enabled,
                }
                for node in sorted(nodes, key=lambda item: item.node_id)
            ],
        }
    )


class PortalRunStore:
    """Durable Portal run/cycle evidence stored beside Project Runner state."""

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

    def close(self) -> None:
        self.connection.close()

    def ensure_run(
        self,
        *,
        run_id: str,
        config_digest: str,
        holder: str,
        now: float,
    ) -> None:
        run_id = run_id.strip()
        holder = holder.strip()
        if not run_id:
            raise ValueError("run_id is required")
        if not holder:
            raise ValueError("run holder is required")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT config_digest, state
                FROM portal_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_runs(
                        run_id, config_digest, holder, state,
                        cycle_count, created_at, updated_at
                    ) VALUES (?, ?, ?, 'READY', 0, ?, ?)
                    """,
                    (run_id, config_digest, holder, now, now),
                )
            else:
                if str(row[0]) != config_digest:
                    raise ValueError("run configuration changed for existing run_id")
                if str(row[1]) == "RUNNING":
                    incomplete = self.connection.execute(
                        """
                        SELECT cycle_number
                        FROM portal_cycles
                        WHERE run_id = ? AND state = 'STARTED'
                        ORDER BY cycle_number DESC
                        LIMIT 1
                        """,
                        (run_id,),
                    ).fetchone()
                    if incomplete is not None:
                        raise ValueError(
                            "run has an incomplete cycle requiring reconciliation"
                        )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def begin_cycle(
        self,
        *,
        run_id: str,
        lane_slots: int,
        now: float,
    ) -> int:
        if lane_slots < 1:
            raise ValueError("Portal cycle requires at least one execution slot")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT cycle_count, state FROM portal_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Portal run does not exist")
            incomplete = self.connection.execute(
                """
                SELECT cycle_number
                FROM portal_cycles
                WHERE run_id = ? AND state = 'STARTED'
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if incomplete is not None:
                raise ValueError(
                    "run has an incomplete cycle requiring reconciliation"
                )
            cycle_number = int(row[0]) + 1
            self.connection.execute(
                """
                INSERT INTO portal_cycles(
                    run_id, cycle_number, state, lane_slots, started_at
                ) VALUES (?, ?, 'STARTED', ?, ?)
                """,
                (run_id, cycle_number, lane_slots, now),
            )
            self.connection.execute(
                """
                UPDATE portal_runs
                SET state = 'RUNNING', cycle_count = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (cycle_number, now, run_id),
            )
            self.connection.commit()
            return cycle_number
        except BaseException:
            self.connection.rollback()
            raise

    def complete_cycle(
        self,
        *,
        run_id: str,
        cycle_number: int,
        cycle: PortfolioCycleResult,
        lanes: tuple[PortalLaneResult, ...],
        progress_made: bool,
        now: float,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT state
                FROM portal_cycles
                WHERE run_id = ? AND cycle_number = ?
                """,
                (run_id, cycle_number),
            ).fetchone()
            if row != ("STARTED",):
                raise ValueError("Portal cycle is not in STARTED state")

            self.connection.executemany(
                """
                INSERT INTO portal_lane_events(
                    run_id, cycle_number, node_id, slot, claimed,
                    queue_state, snapshot_id, frontier_fingerprint,
                    fencing_token, operator_status, route_id, reason,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        cycle_number,
                        lane.node_id,
                        lane.slot,
                        int(lane.claimed),
                        lane.queue_state,
                        lane.snapshot_id,
                        lane.frontier_fingerprint,
                        lane.fencing_token,
                        lane.operator_status,
                        lane.route_id,
                        lane.reason,
                        now,
                    )
                    for lane in lanes
                    if lane.claimed
                ],
            )
            self.connection.execute(
                """
                UPDATE portal_cycles
                SET state = 'COMPLETE',
                    snapshot_id = ?,
                    snapshot_digest = ?,
                    baseline = ?,
                    ready_count = ?,
                    blocked_count = ?,
                    progress_made = ?,
                    completed_at = ?
                WHERE run_id = ? AND cycle_number = ?
                """,
                (
                    cycle.snapshot_id,
                    cycle.snapshot_digest,
                    int(cycle.baseline),
                    cycle.ready_count,
                    cycle.blocked_count,
                    int(progress_made),
                    now,
                    run_id,
                    cycle_number,
                ),
            )
            self.connection.execute(
                """
                UPDATE portal_runs
                SET state = 'READY', updated_at = ?
                WHERE run_id = ?
                """,
                (now, run_id),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def fail_cycle(
        self,
        *,
        run_id: str,
        cycle_number: int,
        reason: str,
        now: float,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                UPDATE portal_cycles
                SET state = 'FAILED', failure_reason = ?, completed_at = ?
                WHERE run_id = ? AND cycle_number = ? AND state = 'STARTED'
                """,
                (reason, now, run_id, cycle_number),
            )
            self.connection.execute(
                """
                UPDATE portal_runs
                SET state = 'FAILED', updated_at = ?
                WHERE run_id = ?
                """,
                (now, run_id),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def summary(self, run_id: str) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT state, cycle_count, config_digest, holder
            FROM portal_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Portal run does not exist")
        cycle_states = {
            str(state): int(count)
            for state, count in self.connection.execute(
                """
                SELECT state, COUNT(*)
                FROM portal_cycles
                WHERE run_id = ?
                GROUP BY state
                ORDER BY state
                """,
                (run_id,),
            ).fetchall()
        }
        lane_states = {
            str(state): int(count)
            for state, count in self.connection.execute(
                """
                SELECT queue_state, COUNT(*)
                FROM portal_lane_events
                WHERE run_id = ?
                GROUP BY queue_state
                ORDER BY queue_state
                """,
                (run_id,),
            ).fetchall()
        }
        lane_events = int(
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM portal_lane_events
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
        )
        return {
            "run_id": run_id,
            "state": str(row[0]),
            "cycles": int(row[1]),
            "config_digest": str(row[2]),
            "holder": str(row[3]),
            "cycle_states": cycle_states,
            "lane_events": lane_events,
            "states": lane_states,
        }


def _execution_slots(
    nodes: tuple[ExecutionNode, ...],
    *,
    max_parallel: int,
) -> tuple[tuple[str, int], ...]:
    if type(max_parallel) is not int or max_parallel < 1:
        raise ValueError("max_parallel must be a positive integer")

    ids = [node.node_id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate execution node id")

    slots: list[tuple[str, int]] = []
    for node in sorted(nodes, key=lambda item: item.node_id):
        if not node.enabled:
            continue
        if node.allowed_lanes:
            raise ValueError(
                "durable Project Runner queue lacks lane metadata; "
                "lane-restricted execution nodes cannot consume it safely"
            )
        for slot in range(node.max_parallel):
            if len(slots) >= max_parallel:
                return tuple(slots)
            slots.append((node.node_id, slot))
    if not slots:
        raise ValueError("no enabled execution capacity is available")
    return tuple(slots)


def _lane_consume(
    *,
    node_id: str,
    slot: int,
    projects: tuple[ProjectDefinition, ...],
    workers: tuple[WorkerDefinition, ...],
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    state_db: Path,
    holder: str,
    lease_ttl: float,
    token: str | None,
    transport: GitHubTransport | None,
    clock: Callable[[], float],
) -> PortalLaneResult:
    lane_holder = f"{holder}:{node_id}:{slot}"
    result: QueueConsumptionResult = consume_next_queued_read_only_work(
        projects=projects,
        workers=workers,
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        worker_registry_digest=worker_registry_digest,
        state_db=state_db,
        holder=lane_holder,
        lease_ttl=lease_ttl,
        token=token,
        transport=transport,
        clock=clock,
    )
    return PortalLaneResult(
        node_id=node_id,
        slot=slot,
        claimed=result.claimed,
        queue_state=result.queue_state,
        snapshot_id=result.snapshot_id,
        frontier_fingerprint=result.frontier_fingerprint,
        fencing_token=result.fencing_token,
        operator_status=result.operator_status,
        route_id=result.route_id,
        reason=result.reason,
    )


def run_portal_once(
    *,
    projects: Iterable[ProjectDefinition],
    dependencies: Iterable[DependencyEdge],
    workers: Iterable[WorkerDefinition],
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    state_db: Path,
    nodes: Iterable[ExecutionNode],
    run_id: str,
    holder: str,
    lease_ttl: float,
    max_parallel: int,
    token: str | None,
    transport: GitHubTransport | None = None,
    private_collision_key: str | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalCycleResult:
    if lease_ttl <= 0:
        raise ValueError("lease_ttl must be positive")

    project_tuple = tuple(projects)
    dependency_tuple = tuple(dependencies)
    worker_tuple = tuple(workers)
    node_tuple = tuple(nodes)
    slots = _execution_slots(node_tuple, max_parallel=max_parallel)

    config_digest = _run_config_digest(
        registry_digest=registry_digest,
        dependency_digest=dependency_digest,
        worker_registry_digest=worker_registry_digest,
        nodes=node_tuple,
        max_parallel=max_parallel,
    )
    run_store = PortalRunStore(Path(state_db))
    cycle_number: int | None = None
    try:
        now = float(clock())
        run_store.ensure_run(
            run_id=run_id,
            config_digest=config_digest,
            holder=holder,
            now=now,
        )
        cycle_number = run_store.begin_cycle(
            run_id=run_id,
            lane_slots=len(slots),
            now=now,
        )
        cycle = collect_and_schedule_portfolio(
            projects=project_tuple,
            dependencies=dependency_tuple,
            workers=worker_tuple,
            registry_digest=registry_digest,
            dependency_digest=dependency_digest,
            worker_registry_digest=worker_registry_digest,
            state_db=Path(state_db),
            token=token,
            transport=transport,
            private_collision_key=private_collision_key,
            clock=clock,
        )

        lane_results: list[PortalLaneResult] = []
        with ThreadPoolExecutor(
            max_workers=len(slots),
            thread_name_prefix="portal-lane",
        ) as executor:
            futures = [
                executor.submit(
                    _lane_consume,
                    node_id=node_id,
                    slot=slot,
                    projects=project_tuple,
                    workers=worker_tuple,
                    registry_digest=registry_digest,
                    dependency_digest=dependency_digest,
                    worker_registry_digest=worker_registry_digest,
                    state_db=Path(state_db),
                    holder=holder,
                    lease_ttl=lease_ttl,
                    token=token,
                    transport=transport,
                    clock=clock,
                )
                for node_id, slot in slots
            ]
            for future in as_completed(futures):
                lane_results.append(future.result())

        lanes = tuple(
            sorted(
                lane_results,
                key=lambda item: (item.node_id, item.slot),
            )
        )
        queue_summary = summarize_queue_state(Path(state_db))
        progress_made = any(
            lane.claimed and lane.queue_state != "NO_WORK"
            for lane in lanes
        )
        run_store.complete_cycle(
            run_id=run_id,
            cycle_number=cycle_number,
            cycle=cycle,
            lanes=lanes,
            progress_made=progress_made,
            now=float(clock()),
        )
        return PortalCycleResult(
            run_id=run_id,
            cycle_number=cycle_number,
            cycle=cycle,
            lanes=lanes,
            queue_summary=queue_summary,
            progress_made=progress_made,
        )
    except BaseException as exc:
        if cycle_number is not None:
            try:
                run_store.fail_cycle(
                    run_id=run_id,
                    cycle_number=cycle_number,
                    reason=f"{type(exc).__name__}: {exc}",
                    now=float(clock()),
                )
            except BaseException:
                pass
        raise
    finally:
        run_store.close()



def run_portal_until_idle(
    *,
    projects: Iterable[ProjectDefinition],
    dependencies: Iterable[DependencyEdge],
    workers: Iterable[WorkerDefinition],
    registry_digest: str,
    dependency_digest: str,
    worker_registry_digest: str,
    state_db: Path,
    nodes: Iterable[ExecutionNode],
    run_id: str,
    holder: str,
    lease_ttl: float,
    max_parallel: int,
    max_cycles: int,
    max_idle_cycles: int,
    poll_seconds: float,
    token: str | None,
    transport: GitHubTransport | None = None,
    private_collision_key: str | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> PortalRunResult:
    """Run bounded Portal cycles until verified idle or a circuit breaker trips.

    Each iteration is a complete durable cycle. Work is never kept only in
    process memory between iterations, so a later invocation can resume the
    same run_id from the shared state database.
    """
    if type(max_cycles) is not int or max_cycles < 1:
        raise ValueError("max_cycles must be a positive integer")
    if type(max_idle_cycles) is not int or max_idle_cycles < 1:
        raise ValueError("max_idle_cycles must be a positive integer")
    if poll_seconds < 0:
        raise ValueError("poll_seconds must be non-negative")

    project_tuple = tuple(projects)
    dependency_tuple = tuple(dependencies)
    worker_tuple = tuple(workers)
    node_tuple = tuple(nodes)

    cycles: list[PortalCycleResult] = []
    idle_cycles = 0
    stop_reason = "MAX_CYCLES"

    for index in range(max_cycles):
        result = run_portal_once(
            projects=project_tuple,
            dependencies=dependency_tuple,
            workers=worker_tuple,
            registry_digest=registry_digest,
            dependency_digest=dependency_digest,
            worker_registry_digest=worker_registry_digest,
            state_db=Path(state_db),
            nodes=node_tuple,
            run_id=run_id,
            holder=holder,
            lease_ttl=lease_ttl,
            max_parallel=max_parallel,
            token=token,
            transport=transport,
            private_collision_key=private_collision_key,
            clock=clock,
        )
        cycles.append(result)

        if result.progress_made:
            idle_cycles = 0
        else:
            idle_cycles += 1
            if idle_cycles >= max_idle_cycles:
                stop_reason = "IDLE"
                break

        if index + 1 < max_cycles and poll_seconds:
            sleep(poll_seconds)

    return PortalRunResult(
        run_id=run_id,
        cycles=tuple(cycles),
        stop_reason=stop_reason,
        idle_cycles=idle_cycles,
    )
