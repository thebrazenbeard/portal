from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping

from runner.github_backend import GitHubTransport
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_wave_scheduler import (
    WaveExecutionBudget,
    collision_keys,
)

from .models import ExecutionNode
from .wave_runtime import PortalWaveStore, prepare_portal_wave
from .worker_backend import ProcessWorkerSpec
from .worker_runtime import run_wave_proposal_workers_once


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_ecosystem_runs (
    session_id TEXT PRIMARY KEY,
    config_digest TEXT NOT NULL,
    generation_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_ecosystem_generations (
    session_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    child_run_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('STARTED', 'COMPLETE', 'FAILED')),
    admitted_count INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    completed_at REAL,
    failure_reason TEXT,
    PRIMARY KEY (session_id, generation),
    UNIQUE (child_run_id),
    FOREIGN KEY (session_id)
        REFERENCES portal_ecosystem_runs(session_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portal_ecosystem_subjects (
    session_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    child_run_id TEXT NOT NULL,
    collision_keys_json TEXT NOT NULL,
    admitted_at REAL NOT NULL,
    PRIMARY KEY (session_id, subject_kind, subject_id),
    FOREIGN KEY (session_id)
        REFERENCES portal_ecosystem_runs(session_id)
        ON DELETE CASCADE
);
"""

_TERMINAL_STATES = frozenset(
    {
        "VERIFIED_COMPLETE",
        "VERIFIED_HELD",
        "FAILED_RETRYABLE",
        "FAILED_DETERMINISTIC",
        "FAILED_PRECONDITION",
        "FAILED_EXECUTION",
    }
)


@dataclass(frozen=True)
class PortalEcosystemResult:
    session_id: str
    generations: int
    admitted: int
    awaiting_promotion: int
    active: int
    terminal: int
    duplicate_admissions: int
    workstreams_remaining: int
    saturated: bool


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _config_digest(
    *,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    nodes: tuple[ExecutionNode, ...],
    budget: WaveExecutionBudget,
    backends: Mapping[str, ProcessWorkerSpec],
) -> str:
    return _digest(
        {
            "schema": "PORTAL_ECOSYSTEM_SESSION_CONFIG_V1",
            "wave_sha256": _file_sha256(wave_path),
            "corpus_sha256": _file_sha256(corpus_path),
            "projects_sha256": _file_sha256(projects_path),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "max_parallel": node.max_parallel,
                    "allowed_lanes": list(node.allowed_lanes),
                    "enabled": node.enabled,
                }
                for node in sorted(nodes, key=lambda value: value.node_id)
            ],
            "budget": {
                "max_parallel": budget.max_parallel,
                "max_per_identity": budget.max_per_identity,
                "max_per_family": budget.max_per_family,
                "max_per_lane": budget.max_per_lane,
            },
            "backends": {
                node_id: {
                    "command": list(spec.command),
                    "timeout_seconds": spec.timeout_seconds,
                    "pass_env": list(spec.pass_env),
                }
                for node_id, spec in sorted(backends.items())
            },
        }
    )


class PortalEcosystemStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        wave_store = PortalWaveStore(self.path)
        wave_store.close()
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

    def ensure_session(
        self,
        *,
        session_id: str,
        config_digest: str,
        now: float,
    ) -> None:
        if not session_id.strip():
            raise ValueError("ecosystem session_id is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT config_digest
                FROM portal_ecosystem_runs
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_ecosystem_runs(
                        session_id, config_digest, generation_count,
                        created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?)
                    """,
                    (session_id, config_digest, now, now),
                )
            elif str(row[0]) != config_digest:
                raise ValueError(
                    "ecosystem session configuration changed for existing session_id"
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def reserve_generation(
        self,
        *,
        session_id: str,
        now: float,
    ) -> tuple[int, str]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            running = self.connection.execute(
                """
                SELECT generation
                FROM portal_ecosystem_generations
                WHERE session_id = ? AND state = 'STARTED'
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if running is not None:
                raise ValueError(
                    "ecosystem session has an incomplete generation requiring reconciliation"
                )
            row = self.connection.execute(
                """
                SELECT generation_count
                FROM portal_ecosystem_runs
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("ecosystem session does not exist")
            generation = int(row[0]) + 1
            child_run_id = f"{session_id}:g{generation:04d}"
            self.connection.execute(
                """
                INSERT INTO portal_ecosystem_generations(
                    session_id, generation, child_run_id, state,
                    admitted_count, started_at
                ) VALUES (?, ?, ?, 'STARTED', 0, ?)
                """,
                (session_id, generation, child_run_id, now),
            )
            self.connection.execute(
                """
                UPDATE portal_ecosystem_runs
                SET generation_count = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (generation, now, session_id),
            )
            self.connection.commit()
            return generation, child_run_id
        except BaseException:
            self.connection.rollback()
            raise

    def complete_generation(
        self,
        *,
        session_id: str,
        generation: int,
        admitted_count: int,
        now: float,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            updated = self.connection.execute(
                """
                UPDATE portal_ecosystem_generations
                SET state = 'COMPLETE',
                    admitted_count = ?,
                    completed_at = ?
                WHERE session_id = ? AND generation = ?
                  AND state = 'STARTED'
                """,
                (admitted_count, now, session_id, generation),
            )
            if updated.rowcount != 1:
                raise ValueError("ecosystem generation is not STARTED")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def fail_generation(
        self,
        *,
        session_id: str,
        generation: int,
        reason: str,
        now: float,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                UPDATE portal_ecosystem_generations
                SET state = 'FAILED',
                    failure_reason = ?,
                    completed_at = ?
                WHERE session_id = ? AND generation = ?
                  AND state = 'STARTED'
                """,
                (reason, now, session_id, generation),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def record_subject(
        self,
        *,
        session_id: str,
        subject_kind: str,
        subject_id: str,
        child_run_id: str,
        collision_keys_value: tuple[str, ...],
        now: float,
    ) -> bool:
        encoded = json.dumps(
            list(collision_keys_value),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                """
                SELECT child_run_id, collision_keys_json
                FROM portal_ecosystem_subjects
                WHERE session_id = ? AND subject_kind = ? AND subject_id = ?
                """,
                (session_id, subject_kind, subject_id),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (child_run_id, encoded):
                    raise ValueError(
                        "ecosystem subject already admitted by a different generation"
                    )
                self.connection.rollback()
                return False
            self.connection.execute(
                """
                INSERT INTO portal_ecosystem_subjects(
                    session_id, subject_kind, subject_id, child_run_id,
                    collision_keys_json, admitted_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    subject_kind,
                    subject_id,
                    child_run_id,
                    encoded,
                    now,
                ),
            )
            self.connection.commit()
            return True
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def subjects(self, session_id: str) -> tuple[dict[str, object], ...]:
        rows = self.connection.execute(
            """
            SELECT
                e.subject_kind,
                e.subject_id,
                e.child_run_id,
                e.collision_keys_json,
                CASE
                    WHEN q.state IS NOT NULL THEN q.state
                    WHEN d.state IS NOT NULL THEN d.state
                    ELSE 'MISSING_CHILD_STATE'
                END AS effective_state
            FROM portal_ecosystem_subjects AS e
            LEFT JOIN portal_wave_deliveries AS d
              ON d.run_id = e.child_run_id
             AND d.subject_id = e.subject_id
            LEFT JOIN portal_wave_proposals AS q
              ON q.run_id = e.child_run_id
             AND q.subject_id = e.subject_id
            WHERE e.session_id = ?
            ORDER BY e.subject_kind, e.subject_id
            """,
            (session_id,),
        ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            try:
                keys = tuple(json.loads(str(row[3])))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "ecosystem collision-key journal is invalid"
                ) from exc
            result.append(
                {
                    "subject_kind": str(row[0]),
                    "subject_id": str(row[1]),
                    "child_run_id": str(row[2]),
                    "collision_keys": keys,
                    "state": str(row[4]),
                }
            )
        return tuple(result)

    def excluded_subjects(
        self,
        session_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            (str(item["subject_kind"]), str(item["subject_id"]))
            for item in self.subjects(session_id)
        )

    def active_collision_keys(self, session_id: str) -> tuple[str, ...]:
        keys: set[str] = set()
        for item in self.subjects(session_id):
            if str(item["state"]) in _TERMINAL_STATES:
                continue
            keys.update(str(value) for value in item["collision_keys"])
        return tuple(sorted(keys))

    def summary(self, session_id: str) -> dict[str, object]:
        run = self.connection.execute(
            """
            SELECT generation_count
            FROM portal_ecosystem_runs
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if run is None:
            raise ValueError("ecosystem session does not exist")
        subjects = self.subjects(session_id)
        states: dict[str, int] = {}
        for item in subjects:
            state = str(item["state"])
            states[state] = states.get(state, 0) + 1
        return {
            "session_id": session_id,
            "generations": int(run[0]),
            "admitted_subjects": len(subjects),
            "effective_states": dict(sorted(states.items())),
            "active_collision_keys": len(self.active_collision_keys(session_id)),
        }


def run_ecosystem_proposal_generations(
    *,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    state_db: Path,
    nodes: tuple[ExecutionNode, ...],
    budget: WaveExecutionBudget,
    backends: Mapping[str, ProcessWorkerSpec],
    workspace_root: Path,
    session_id: str,
    holder: str,
    lease_ttl: float,
    delivery_lease_ttl: float,
    holder_prefix: str,
    max_generations: int,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalEcosystemResult:
    """Continuously refill repository proposal lanes across one durable session.

    Repository subjects are admitted once per session. Nonterminal subjects keep
    their collision keys occupied. Multi-repository workstreams are deliberately
    left for the separate workstream coordinator rather than being flattened
    into repository jobs.
    """

    if type(max_generations) is not int or max_generations < 1:
        raise ValueError("max_generations must be a positive integer")
    wave_path = Path(wave_path)
    corpus_path = Path(corpus_path)
    projects_path = Path(projects_path)
    state_db = Path(state_db)
    workspace_root = Path(workspace_root)

    wave = load_advancement_wave(wave_path)
    item_by_subject = {
        (item.subject_kind, item.subject_id): item
        for item in wave.items
    }
    workstreams = tuple(
        (item.subject_kind, item.subject_id)
        for item in wave.items
        if item.subject_kind != "repository"
        and item.execution_state == "QUEUED"
    )
    config_digest = _config_digest(
        wave_path=wave_path,
        corpus_path=corpus_path,
        projects_path=projects_path,
        nodes=nodes,
        budget=budget,
        backends=backends,
    )

    store = PortalEcosystemStore(state_db)
    try:
        store.ensure_session(
            session_id=session_id,
            config_digest=config_digest,
            now=float(clock()),
        )
    finally:
        store.close()

    admitted_this_call = 0
    duplicates = 0
    completed_generations = 0
    saturated = False

    for _ in range(max_generations):
        store = PortalEcosystemStore(state_db)
        try:
            excluded = set(store.excluded_subjects(session_id))
            occupied = store.active_collision_keys(session_id)
            generation, child_run_id = store.reserve_generation(
                session_id=session_id,
                now=float(clock()),
            )
        finally:
            store.close()

        excluded.update(workstreams)
        try:
            prepared = prepare_portal_wave(
                wave_path=wave_path,
                corpus_path=corpus_path,
                projects_path=projects_path,
                state_db=state_db,
                nodes=nodes,
                budget=budget,
                run_id=child_run_id,
                holder=holder,
                lease_ttl=lease_ttl,
                token=token,
                occupied_collision_keys=occupied,
                excluded_subjects=tuple(sorted(excluded)),
                transport=transport,
                clock=clock,
            )

            store = PortalEcosystemStore(state_db)
            try:
                for packet in prepared.packets:
                    item = item_by_subject.get(("repository", packet.subject_id))
                    if item is None:
                        raise ValueError(
                            "prepared repository packet is absent from advancement wave"
                        )
                    inserted = store.record_subject(
                        session_id=session_id,
                        subject_kind="repository",
                        subject_id=packet.subject_id,
                        child_run_id=child_run_id,
                        collision_keys_value=collision_keys(item),
                        now=float(clock()),
                    )
                    if inserted:
                        admitted_this_call += 1
                    else:
                        duplicates += 1
            finally:
                store.close()

            if prepared.packets:
                run_wave_proposal_workers_once(
                    state_db=state_db,
                    run_id=child_run_id,
                    nodes=nodes,
                    backends=backends,
                    workspace_root=workspace_root,
                    holder_prefix=holder_prefix,
                    delivery_lease_ttl=delivery_lease_ttl,
                    token=token,
                    transport=transport,
                    clock=clock,
                )

            store = PortalEcosystemStore(state_db)
            try:
                store.complete_generation(
                    session_id=session_id,
                    generation=generation,
                    admitted_count=len(prepared.packets),
                    now=float(clock()),
                )
            finally:
                store.close()
            completed_generations += 1

            if not prepared.packets:
                saturated = True
                break
        except BaseException as exc:
            store = PortalEcosystemStore(state_db)
            try:
                store.fail_generation(
                    session_id=session_id,
                    generation=generation,
                    reason=f"{type(exc).__name__}: {exc}",
                    now=float(clock()),
                )
            finally:
                store.close()
            raise

    store = PortalEcosystemStore(state_db)
    try:
        summary = store.summary(session_id)
    finally:
        store.close()
    states = dict(summary["effective_states"])
    awaiting = int(states.get("AWAITING_PROMOTION", 0))
    terminal = sum(
        count for state, count in states.items()
        if state in _TERMINAL_STATES
    )
    active = int(summary["admitted_subjects"]) - terminal

    return PortalEcosystemResult(
        session_id=session_id,
        generations=completed_generations,
        admitted=admitted_this_call,
        awaiting_promotion=awaiting,
        active=active,
        terminal=terminal,
        duplicate_admissions=duplicates,
        workstreams_remaining=len(workstreams),
        saturated=saturated,
    )
