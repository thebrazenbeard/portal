from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable

from runner.github_backend import GitHubTransport
from runner.portfolio_advancement import load_advancement_wave
from runner.portfolio_corpus import load_portfolio_corpus
from runner.portfolio_operator_binding import bind_wave_to_operator_registry
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.portfolio_plan_binding import (
    build_bound_wave_plan_payload,
    write_bound_wave_plan,
)
from runner.portfolio_wave_scheduler import WaveExecutionBudget
from runner.registry import load_project_snapshot

from .coordinator import plan_portal_wave
from .models import ExecutionNode


@dataclass(frozen=True)
class PortalWavePacket:
    run_id: str
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    node_id: str
    lane_id: str
    state: str
    plan_sha256: str
    fencing_token: int
    lineage_id: str
    work_fingerprint: str
    action: str
    effect_ceiling: str
    review_gate: str
    frontier: str | None
    lead_identity: str
    reviewer_identities: tuple[str, ...]


@dataclass(frozen=True)
class PortalWavePreparationResult:
    run_id: str
    plan_sha256: str
    plan_path: Path
    assigned: int
    claimed: int
    held: int
    packets: tuple[PortalWavePacket, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_wave_runs (
    run_id TEXT PRIMARY KEY,
    config_digest TEXT NOT NULL,
    wave_sha256 TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    holder TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_wave_packets (
    run_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    ref TEXT NOT NULL,
    exact_head TEXT NOT NULL,
    node_id TEXT NOT NULL,
    lane_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('CLAIMED', 'HELD')),
    plan_sha256 TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL,
    effect_ceiling TEXT NOT NULL,
    review_gate TEXT NOT NULL,
    frontier TEXT,
    lead_identity TEXT NOT NULL,
    reviewer_identities_json TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, subject_id),
    FOREIGN KEY (run_id) REFERENCES portal_wave_runs(run_id) ON DELETE CASCADE
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


def _node_payload(nodes: tuple[ExecutionNode, ...]) -> list[dict[str, object]]:
    return [
        {
            "node_id": node.node_id,
            "max_parallel": node.max_parallel,
            "allowed_lanes": list(node.allowed_lanes),
            "enabled": node.enabled,
        }
        for node in sorted(nodes, key=lambda value: value.node_id)
    ]


class PortalWaveStore:
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
        wave_sha256: str,
        plan_sha256: str,
        holder: str,
        now: float,
    ) -> None:
        run_id = run_id.strip()
        holder = holder.strip()
        if not run_id:
            raise ValueError("wave run_id is required")
        if not holder:
            raise ValueError("wave holder is required")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT config_digest, wave_sha256, plan_sha256, holder
                FROM portal_wave_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_wave_runs(
                        run_id, config_digest, wave_sha256, plan_sha256,
                        holder, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        config_digest,
                        wave_sha256,
                        plan_sha256,
                        holder,
                        now,
                        now,
                    ),
                )
            else:
                expected = (
                    config_digest,
                    wave_sha256,
                    plan_sha256,
                    holder,
                )
                observed = tuple(str(value) for value in row)
                if observed != expected:
                    raise ValueError(
                        "wave run configuration changed for existing run_id"
                    )
                self.connection.execute(
                    """
                    UPDATE portal_wave_runs
                    SET updated_at = ?
                    WHERE run_id = ?
                    """,
                    (now, run_id),
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def record_packet(
        self,
        packet: PortalWavePacket,
        *,
        reason: str | None,
        now: float,
    ) -> PortalWavePacket:
        reviewers_json = json.dumps(
            list(packet.reviewer_identities),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT
                    repository, ref, exact_head, node_id, lane_id, state,
                    plan_sha256, fencing_token, lineage_id, work_fingerprint,
                    action, effect_ceiling, review_gate, frontier,
                    lead_identity, reviewer_identities_json
                FROM portal_wave_packets
                WHERE run_id = ? AND subject_id = ?
                """,
                (packet.run_id, packet.subject_id),
            ).fetchone()
            immutable = (
                packet.repository,
                packet.ref,
                packet.exact_head,
                packet.node_id,
                packet.lane_id,
                packet.state,
                packet.plan_sha256,
                packet.fencing_token,
                packet.lineage_id,
                packet.work_fingerprint,
                packet.action,
                packet.effect_ceiling,
                packet.review_gate,
                packet.frontier,
                packet.lead_identity,
                reviewers_json,
            )
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO portal_wave_packets(
                        run_id, subject_id, repository, ref, exact_head,
                        node_id, lane_id, state, plan_sha256, fencing_token,
                        lineage_id, work_fingerprint, action, effect_ceiling,
                        review_gate, frontier, lead_identity,
                        reviewer_identities_json, reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        packet.run_id,
                        packet.subject_id,
                        *immutable,
                        reason,
                        now,
                        now,
                    ),
                )
            elif tuple(row) != immutable:
                raise ValueError(
                    "durable wave packet diverges from existing exact claim"
                )
            else:
                self.connection.execute(
                    """
                    UPDATE portal_wave_packets
                    SET updated_at = ?
                    WHERE run_id = ? AND subject_id = ?
                    """,
                    (now, packet.run_id, packet.subject_id),
                )
            self.connection.commit()
            return packet
        except BaseException:
            self.connection.rollback()
            raise

    def summary(self, run_id: str) -> dict[str, object]:
        exists = self.connection.execute(
            "SELECT 1 FROM portal_wave_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("Portal wave run does not exist")
        rows = self.connection.execute(
            """
            SELECT state, COUNT(*)
            FROM portal_wave_packets
            WHERE run_id = ?
            GROUP BY state
            ORDER BY state
            """,
            (run_id,),
        ).fetchall()
        states = {str(state): int(count) for state, count in rows}
        return {
            "run_id": run_id,
            "packets": sum(states.values()),
            "states": states,
        }


def prepare_portal_wave(
    *,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    state_db: Path,
    nodes: Iterable[ExecutionNode],
    budget: WaveExecutionBudget,
    run_id: str,
    holder: str,
    lease_ttl: float,
    token: str | None,
    occupied_collision_keys: Iterable[str] = (),
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalWavePreparationResult:
    """Prepare exact, fenced work packets for node-assigned wave subjects.

    Preparation performs only read-only currentness checks and durable claim/
    outbox writes. It does not execute project work or mutate target repositories.
    """

    wave_path = Path(wave_path)
    corpus_path = Path(corpus_path)
    projects_path = Path(projects_path)
    state_db = Path(state_db)
    node_tuple = tuple(nodes)
    occupied_tuple = tuple(occupied_collision_keys)

    wave = load_advancement_wave(wave_path)
    corpus = load_portfolio_corpus(corpus_path, public_safe=True)
    registry = load_project_snapshot(projects_path)
    binding_report = bind_wave_to_operator_registry(
        wave,
        corpus,
        registry,
        public_safe=True,
    )

    portal_plan = plan_portal_wave(
        wave,
        budget=budget,
        nodes=node_tuple,
        occupied_collision_keys=occupied_tuple,
    )
    plan_payload = build_bound_wave_plan_payload(
        wave_path,
        budget=budget,
        occupied_collision_keys=occupied_tuple,
    )
    plan_binding = plan_payload.get("plan_binding")
    if not isinstance(plan_binding, dict):
        raise ValueError("bound plan is missing plan_binding")
    plan_sha256 = str(plan_binding.get("sha256", ""))
    if len(plan_sha256) != 64:
        raise ValueError("bound plan sha256 is invalid")
    wave_binding = plan_payload.get("wave_binding")
    if not isinstance(wave_binding, dict):
        raise ValueError("bound plan is missing wave_binding")
    wave_sha256 = str(wave_binding.get("sha256", ""))
    if len(wave_sha256) != 64:
        raise ValueError("bound wave sha256 is invalid")

    plans_root = state_db.parent / "portal-plans"
    plan_path = plans_root / f"{plan_sha256}.json"
    if plan_path.exists():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing != plan_payload:
            raise ValueError("existing durable plan file diverges from exact plan")
    else:
        write_bound_wave_plan(plan_path, plan_payload)

    config_digest = _canonical_digest(
        {
            "schema": "PORTAL_WAVE_RUN_CONFIG_V1",
            "wave_sha256": wave_sha256,
            "corpus_sha256": corpus.sha256,
            "operator_registry_sha256": registry.sha256,
            "plan_sha256": plan_sha256,
            "nodes": _node_payload(node_tuple),
            "budget": {
                "max_parallel": budget.max_parallel,
                "max_per_identity": budget.max_per_identity,
                "max_per_family": budget.max_per_family,
                "max_per_lane": budget.max_per_lane,
            },
            "occupied_collision_keys": sorted(occupied_tuple),
        }
    )

    decisions = {
        (item.subject_kind, item.subject_id): item
        for item in binding_report.decisions
    }
    wave_items = {
        (item.subject_kind, item.subject_id): item
        for item in wave.items
    }
    records = {record.id: record for record in corpus.records}

    store = PortalWaveStore(state_db)
    packets: list[PortalWavePacket] = []
    held = 0
    try:
        now = float(clock())
        store.ensure_run(
            run_id=run_id,
            config_digest=config_digest,
            wave_sha256=wave_sha256,
            plan_sha256=plan_sha256,
            holder=holder,
            now=now,
        )

        for assignment in portal_plan.assignments:
            key = (assignment.subject_kind, assignment.subject_id)
            binding = decisions.get(key)
            item = wave_items.get(key)
            if binding is None or item is None:
                raise ValueError("Portal assignment lacks exact wave/binding evidence")
            if assignment.subject_kind != "repository":
                held += 1
                continue
            if binding.state != "BOUND" or binding.repository is None:
                held += 1
                continue

            record = records.get(assignment.subject_id)
            if record is None:
                raise ValueError("assigned repository is missing from corpus")

            claim = claim_bound_plan_subject(
                plan_path=plan_path,
                wave_path=wave_path,
                corpus_path=corpus_path,
                projects_path=projects_path,
                subject_id=assignment.subject_id,
                state_db=state_db,
                holder=holder,
                lease_ttl=lease_ttl,
                allowed_repositories=(record.repository,),
                token=token,
                transport=transport,
                clock=clock,
            )
            packet = PortalWavePacket(
                run_id=run_id,
                subject_id=assignment.subject_id,
                repository=claim.repository,
                ref=claim.ref,
                exact_head=claim.exact_head,
                node_id=assignment.node_id,
                lane_id=assignment.lane_id,
                state="CLAIMED",
                plan_sha256=plan_sha256,
                fencing_token=claim.fencing_token,
                lineage_id=claim.lineage_id,
                work_fingerprint=claim.work_fingerprint,
                action=assignment.action,
                effect_ceiling=assignment.effect_ceiling,
                review_gate=item.review_gate,
                frontier=item.frontier,
                lead_identity=item.lead_identity,
                reviewer_identities=item.reviewer_identities,
            )
            store.record_packet(
                packet,
                reason="exact Project Runner plan/head/fence claim acquired",
                now=float(clock()),
            )
            packets.append(packet)
    finally:
        store.close()

    return PortalWavePreparationResult(
        run_id=run_id,
        plan_sha256=plan_sha256,
        plan_path=plan_path,
        assigned=len(portal_plan.assignments),
        claimed=len(packets),
        held=held,
        packets=tuple(sorted(packets, key=lambda item: item.subject_id)),
    )
