from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Iterable

from runner.github_backend import GitHubRestTransport, GitHubTransport
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


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUCCESS_RECEIPTS = {
    "SUCCEEDED_SOURCE_CHANGE",
    "SUCCEEDED_NO_EFFECT",
    "HELD",
}
_FAILURE_RECEIPTS = {
    "FAILED_RETRYABLE",
    "FAILED_DETERMINISTIC",
    "OUTCOME_UNKNOWN",
}
_RECEIPT_CLASSES = _SUCCESS_RECEIPTS | _FAILURE_RECEIPTS


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


@dataclass(frozen=True)
class PortalWaveDeliveryClaim:
    run_id: str
    subject_id: str
    node_id: str
    holder: str
    fencing_token: int
    lease_expires_at: float
    payload: dict[str, object]


@dataclass(frozen=True)
class PortalWaveReceipt:
    run_id: str
    subject_id: str
    node_id: str
    state: str
    receipt_class: str
    receipt_sha256: str
    result_repository: str | None
    result_ref: str | None
    result_head: str | None


@dataclass(frozen=True)
class PortalWaveVerificationResult:
    run_id: str
    subject_id: str
    state: str
    result_repository: str | None
    result_ref: str | None
    result_head: str | None
    reason: str


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

CREATE TABLE IF NOT EXISTS portal_wave_deliveries (
    run_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'PENDING',
            'DELIVERED',
            'RECEIPT_RECORDED',
            'FAILED_RETRYABLE',
            'FAILED_DETERMINISTIC',
            'OUTCOME_UNKNOWN',
            'VERIFICATION_STALE',
            'VERIFIED_COMPLETE',
            'VERIFIED_HELD'
        )
    ),
    holder TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    lease_expires_at REAL,
    receipt_class TEXT,
    receipt_sha256 TEXT,
    result_repository TEXT,
    result_ref TEXT,
    result_head TEXT,
    evidence_sha256 TEXT,
    reason TEXT,
    verifier TEXT,
    verified_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (run_id, subject_id),
    FOREIGN KEY (run_id, subject_id)
        REFERENCES portal_wave_packets(run_id, subject_id)
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


def _read_exact_ref(
    *,
    repository: str,
    ref: str,
    token: str | None,
    transport: GitHubTransport | None,
) -> str:
    source = transport or GitHubRestTransport(token=token)
    head = source.read_ref(repository, ref)
    if _SHA40.fullmatch(head) is None:
        raise ValueError("repository currentness read returned non-exact head")
    return head


def _delivery_payload(
    packet: PortalWavePacket,
    *,
    delivery_fencing_token: int,
) -> dict[str, object]:
    return {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "run_id": packet.run_id,
        "subject_id": packet.subject_id,
        "repository": packet.repository,
        "source_ref": packet.ref,
        "exact_head": packet.exact_head,
        "node_id": packet.node_id,
        "lane_id": packet.lane_id,
        "action": packet.action,
        "frontier": packet.frontier,
        "effect_ceiling": packet.effect_ceiling,
        "review_gate": packet.review_gate,
        "lead_identity": packet.lead_identity,
        "reviewer_identities": list(packet.reviewer_identities),
        "plan_sha256": packet.plan_sha256,
        "project_runner_claim": {
            "fencing_token": packet.fencing_token,
            "lineage_id": packet.lineage_id,
            "work_fingerprint": packet.work_fingerprint,
        },
        "delivery_fencing_token": delivery_fencing_token,
        "protected_effects_authorized": False,
        "target_ref_mutation_authorized": False,
        "completion_contract": {
            "worker_receipt_is_completion": False,
            "independent_verification_required": True,
            "source_change_requires_non_default_result_ref": True,
            "source_change_requires_exact_result_head": True,
            "original_source_must_remain_exact_for_completion": True,
        },
    }


def _packet_from_row(run_id: str, subject_id: str, row: tuple[object, ...]) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=str(row[0]),
        ref=str(row[1]),
        exact_head=str(row[2]),
        node_id=str(row[3]),
        lane_id=str(row[4]),
        state=str(row[5]),
        plan_sha256=str(row[6]),
        fencing_token=int(row[7]),
        lineage_id=str(row[8]),
        work_fingerprint=str(row[9]),
        action=str(row[10]),
        effect_ceiling=str(row[11]),
        review_gate=str(row[12]),
        frontier=(str(row[13]) if row[13] is not None else None),
        lead_identity=str(row[14]),
        reviewer_identities=tuple(json.loads(str(row[15]))),
    )


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

            self.connection.execute(
                """
                INSERT OR IGNORE INTO portal_wave_deliveries(
                    run_id, subject_id, node_id, state,
                    fencing_token, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?)
                """,
                (
                    packet.run_id,
                    packet.subject_id,
                    packet.node_id,
                    now,
                    now,
                ),
            )
            delivery_node = self.connection.execute(
                """
                SELECT node_id
                FROM portal_wave_deliveries
                WHERE run_id = ? AND subject_id = ?
                """,
                (packet.run_id, packet.subject_id),
            ).fetchone()
            if delivery_node != (packet.node_id,):
                raise ValueError(
                    "durable delivery node diverges from exact wave packet"
                )

            self.connection.commit()
            return packet
        except BaseException:
            self.connection.rollback()
            raise

    def claim_delivery(
        self,
        *,
        run_id: str,
        node_id: str,
        holder: str,
        now: float,
        ttl: float,
    ) -> PortalWaveDeliveryClaim | None:
        run_id = run_id.strip()
        node_id = node_id.strip()
        holder = holder.strip()
        if not run_id or not node_id or not holder:
            raise ValueError("run_id, node_id, and delivery holder are required")
        if ttl <= 0:
            raise ValueError("delivery ttl must be positive")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                """
                SELECT
                    p.subject_id,
                    p.repository, p.ref, p.exact_head, p.node_id, p.lane_id,
                    p.state, p.plan_sha256, p.fencing_token, p.lineage_id,
                    p.work_fingerprint, p.action, p.effect_ceiling,
                    p.review_gate, p.frontier, p.lead_identity,
                    p.reviewer_identities_json,
                    d.state, d.holder, d.fencing_token, d.lease_expires_at
                FROM portal_wave_packets AS p
                JOIN portal_wave_deliveries AS d
                  ON d.run_id = p.run_id
                 AND d.subject_id = p.subject_id
                WHERE p.run_id = ?
                  AND p.node_id = ?
                  AND p.state = 'CLAIMED'
                ORDER BY p.subject_id
                """,
                (run_id, node_id),
            ).fetchall()

            selected: tuple[object, ...] | None = None
            for row in rows:
                delivery_state = str(row[17])
                lease_expires_at = (
                    float(row[20]) if row[20] is not None else None
                )
                if delivery_state == "DELIVERED":
                    if lease_expires_at is not None and lease_expires_at <= now:
                        self.connection.execute(
                            """
                            UPDATE portal_wave_deliveries
                            SET state = 'OUTCOME_UNKNOWN',
                                reason = ?,
                                lease_expires_at = NULL,
                                updated_at = ?
                            WHERE run_id = ? AND subject_id = ?
                              AND state = 'DELIVERED'
                            """,
                            (
                                "delivery lease expired; effect outcome requires reconciliation",
                                now,
                                run_id,
                                str(row[0]),
                            ),
                        )
                    continue
                if delivery_state == "PENDING":
                    selected = row
                    break

            if selected is None:
                self.connection.commit()
                return None

            subject_id = str(selected[0])
            new_fence = int(selected[19]) + 1
            expires_at = now + ttl
            cursor = self.connection.execute(
                """
                UPDATE portal_wave_deliveries
                SET state = 'DELIVERED',
                    holder = ?,
                    fencing_token = ?,
                    lease_expires_at = ?,
                    reason = NULL,
                    updated_at = ?
                WHERE run_id = ? AND subject_id = ?
                  AND state = 'PENDING'
                """,
                (
                    holder,
                    new_fence,
                    expires_at,
                    now,
                    run_id,
                    subject_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("delivery claim changed concurrently")

            packet = _packet_from_row(
                run_id,
                subject_id,
                tuple(selected[1:17]),
            )
            payload = _delivery_payload(
                packet,
                delivery_fencing_token=new_fence,
            )
            self.connection.commit()
            return PortalWaveDeliveryClaim(
                run_id=run_id,
                subject_id=subject_id,
                node_id=node_id,
                holder=holder,
                fencing_token=new_fence,
                lease_expires_at=expires_at,
                payload=payload,
            )
        except BaseException:
            self.connection.rollback()
            raise

    def record_delivery_receipt(
        self,
        *,
        run_id: str,
        subject_id: str,
        node_id: str,
        holder: str,
        expected_fencing_token: int,
        receipt_class: str,
        result_repository: str | None,
        result_ref: str | None,
        result_head: str | None,
        evidence_sha256: str,
        reason: str,
        now: float,
    ) -> PortalWaveReceipt:
        receipt_class = receipt_class.strip()
        if receipt_class not in _RECEIPT_CLASSES:
            raise ValueError("unsupported delivery receipt class")
        if _SHA256.fullmatch(evidence_sha256) is None:
            raise ValueError("receipt evidence_sha256 must be lowercase SHA-256")
        if not reason.strip():
            raise ValueError("delivery receipt reason is required")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT
                    p.repository, p.ref, p.node_id,
                    d.state, d.holder, d.fencing_token, d.lease_expires_at,
                    d.receipt_class, d.receipt_sha256,
                    d.result_repository, d.result_ref, d.result_head
                FROM portal_wave_packets AS p
                JOIN portal_wave_deliveries AS d
                  ON d.run_id = p.run_id
                 AND d.subject_id = p.subject_id
                WHERE p.run_id = ? AND p.subject_id = ?
                """,
                (run_id, subject_id),
            ).fetchone()
            if row is None:
                raise ValueError("delivery receipt subject does not exist")
            repository = str(row[0])
            source_ref = str(row[1])
            packet_node = str(row[2])
            state = str(row[3])

            if packet_node != node_id:
                raise ValueError("delivery receipt node does not match packet")

            canonical_receipt = {
                "schema": "PORTAL_WAVE_WORKER_RECEIPT_V1",
                "run_id": run_id,
                "subject_id": subject_id,
                "node_id": node_id,
                "holder": holder,
                "fencing_token": expected_fencing_token,
                "receipt_class": receipt_class,
                "result_repository": result_repository,
                "result_ref": result_ref,
                "result_head": result_head,
                "evidence_sha256": evidence_sha256,
                "reason": reason,
            }
            receipt_sha256 = _canonical_digest(canonical_receipt)

            if state in {
                "RECEIPT_RECORDED",
                "FAILED_RETRYABLE",
                "FAILED_DETERMINISTIC",
                "OUTCOME_UNKNOWN",
                "VERIFICATION_STALE",
                "VERIFIED_COMPLETE",
                "VERIFIED_HELD",
            }:
                existing = (
                    str(row[7]) if row[7] is not None else None,
                    str(row[8]) if row[8] is not None else None,
                    str(row[9]) if row[9] is not None else None,
                    str(row[10]) if row[10] is not None else None,
                    str(row[11]) if row[11] is not None else None,
                )
                requested = (
                    receipt_class,
                    receipt_sha256,
                    result_repository,
                    result_ref,
                    result_head,
                )
                if existing != requested:
                    raise ValueError(
                        "delivery already has a different receipt or terminal state"
                    )
                self.connection.commit()
                return PortalWaveReceipt(
                    run_id=run_id,
                    subject_id=subject_id,
                    node_id=node_id,
                    state=state,
                    receipt_class=receipt_class,
                    receipt_sha256=receipt_sha256,
                    result_repository=result_repository,
                    result_ref=result_ref,
                    result_head=result_head,
                )

            if state != "DELIVERED":
                raise ValueError("delivery is not actively leased")
            if row[4] != holder or int(row[5]) != expected_fencing_token:
                raise ValueError("delivery fence does not match active lease")
            lease_expires_at = (
                float(row[6]) if row[6] is not None else None
            )
            if lease_expires_at is None or lease_expires_at <= now:
                self.connection.execute(
                    """
                    UPDATE portal_wave_deliveries
                    SET state = 'OUTCOME_UNKNOWN',
                        reason = ?,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE run_id = ? AND subject_id = ?
                    """,
                    (
                        "receipt arrived after delivery lease expired",
                        now,
                        run_id,
                        subject_id,
                    ),
                )
                self.connection.commit()
                raise ValueError(
                    "delivery lease expired before receipt; reconciliation required"
                )

            if receipt_class == "SUCCEEDED_SOURCE_CHANGE":
                if result_repository != repository:
                    raise ValueError(
                        "source-change receipt must remain in packet repository"
                    )
                if not result_ref or result_ref == source_ref:
                    raise ValueError(
                        "source-change receipt requires a non-default result ref"
                    )
                if result_head is None or _SHA40.fullmatch(result_head) is None:
                    raise ValueError(
                        "source-change receipt requires an exact result head"
                    )
            elif receipt_class in {"SUCCEEDED_NO_EFFECT", "HELD"}:
                if any(
                    value is not None
                    for value in (result_repository, result_ref, result_head)
                ):
                    raise ValueError(
                        "no-effect/held receipt must not claim a result ref"
                    )
            elif any(
                value is not None
                for value in (result_repository, result_ref, result_head)
            ):
                raise ValueError(
                    "failed/unknown receipt must not claim a result ref"
                )

            if receipt_class in _SUCCESS_RECEIPTS:
                next_state = "RECEIPT_RECORDED"
            else:
                next_state = receipt_class

            self.connection.execute(
                """
                UPDATE portal_wave_deliveries
                SET state = ?,
                    receipt_class = ?,
                    receipt_sha256 = ?,
                    result_repository = ?,
                    result_ref = ?,
                    result_head = ?,
                    evidence_sha256 = ?,
                    reason = ?,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE run_id = ? AND subject_id = ?
                """,
                (
                    next_state,
                    receipt_class,
                    receipt_sha256,
                    result_repository,
                    result_ref,
                    result_head,
                    evidence_sha256,
                    reason,
                    now,
                    run_id,
                    subject_id,
                ),
            )
            self.connection.commit()
            return PortalWaveReceipt(
                run_id=run_id,
                subject_id=subject_id,
                node_id=node_id,
                state=next_state,
                receipt_class=receipt_class,
                receipt_sha256=receipt_sha256,
                result_repository=result_repository,
                result_ref=result_ref,
                result_head=result_head,
            )
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def load_delivery(
        self,
        *,
        run_id: str,
        subject_id: str,
    ) -> dict[str, object]:
        row = self.connection.execute(
            """
            SELECT
                p.repository, p.ref, p.exact_head, p.node_id,
                d.state, d.receipt_class, d.receipt_sha256,
                d.result_repository, d.result_ref, d.result_head,
                d.evidence_sha256, d.reason
            FROM portal_wave_packets AS p
            JOIN portal_wave_deliveries AS d
              ON d.run_id = p.run_id
             AND d.subject_id = p.subject_id
            WHERE p.run_id = ? AND p.subject_id = ?
            """,
            (run_id, subject_id),
        ).fetchone()
        if row is None:
            raise ValueError("Portal wave delivery does not exist")
        return {
            "repository": str(row[0]),
            "source_ref": str(row[1]),
            "exact_head": str(row[2]),
            "node_id": str(row[3]),
            "state": str(row[4]),
            "receipt_class": (
                str(row[5]) if row[5] is not None else None
            ),
            "receipt_sha256": (
                str(row[6]) if row[6] is not None else None
            ),
            "result_repository": (
                str(row[7]) if row[7] is not None else None
            ),
            "result_ref": str(row[8]) if row[8] is not None else None,
            "result_head": str(row[9]) if row[9] is not None else None,
            "evidence_sha256": (
                str(row[10]) if row[10] is not None else None
            ),
            "reason": str(row[11]) if row[11] is not None else "",
        }

    def finalize_verification(
        self,
        *,
        run_id: str,
        subject_id: str,
        expected_receipt_sha256: str,
        state: str,
        verifier: str,
        reason: str,
        now: float,
    ) -> None:
        if state not in {
            "VERIFIED_COMPLETE",
            "VERIFIED_HELD",
            "VERIFICATION_STALE",
        }:
            raise ValueError("unsupported verification terminal state")
        if not verifier.strip():
            raise ValueError("verification requires verifier identity")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT state, receipt_sha256
                FROM portal_wave_deliveries
                WHERE run_id = ? AND subject_id = ?
                """,
                (run_id, subject_id),
            ).fetchone()
            if row is None:
                raise ValueError("Portal wave delivery does not exist")
            if str(row[0]) != "RECEIPT_RECORDED":
                raise ValueError("delivery is not awaiting verification")
            if str(row[1]) != expected_receipt_sha256:
                raise ValueError("delivery receipt changed before verification")
            self.connection.execute(
                """
                UPDATE portal_wave_deliveries
                SET state = ?,
                    verifier = ?,
                    verified_at = ?,
                    reason = ?,
                    updated_at = ?
                WHERE run_id = ? AND subject_id = ?
                """,
                (
                    state,
                    verifier,
                    now,
                    reason,
                    now,
                    run_id,
                    subject_id,
                ),
            )
            self.connection.commit()
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
        delivery_rows = self.connection.execute(
            """
            SELECT state, COUNT(*)
            FROM portal_wave_deliveries
            WHERE run_id = ?
            GROUP BY state
            ORDER BY state
            """,
            (run_id,),
        ).fetchall()
        delivery_states = {
            str(state): int(count) for state, count in delivery_rows
        }
        return {
            "run_id": run_id,
            "packets": sum(states.values()),
            "states": states,
            "delivery_states": delivery_states,
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
                raise ValueError(
                    "Portal assignment lacks exact wave/binding evidence"
                )
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


def verify_portal_wave_delivery(
    *,
    state_db: Path,
    run_id: str,
    subject_id: str,
    verifier: str,
    token: str | None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> PortalWaveVerificationResult:
    """Independently verify a worker receipt before freeing the semantic lane."""

    store = PortalWaveStore(Path(state_db))
    try:
        record = store.load_delivery(
            run_id=run_id,
            subject_id=subject_id,
        )
        if record["state"] != "RECEIPT_RECORDED":
            raise ValueError("delivery is not awaiting verification")
        receipt_sha256 = str(record["receipt_sha256"])
        receipt_class = str(record["receipt_class"])

        source_head = _read_exact_ref(
            repository=str(record["repository"]),
            ref=str(record["source_ref"]),
            token=token,
            transport=transport,
        )
        if source_head != record["exact_head"]:
            state = "VERIFICATION_STALE"
            reason = (
                "original exact source moved before completion verification"
            )
        elif receipt_class == "SUCCEEDED_SOURCE_CHANGE":
            result_repository = str(record["result_repository"])
            result_ref = str(record["result_ref"])
            expected_result_head = str(record["result_head"])
            observed_result_head = _read_exact_ref(
                repository=result_repository,
                ref=result_ref,
                token=token,
                transport=transport,
            )
            if observed_result_head != expected_result_head:
                state = "VERIFICATION_STALE"
                reason = "result ref moved before completion verification"
            else:
                state = "VERIFIED_COMPLETE"
                reason = (
                    "original source remained exact and result ref/head "
                    "verified independently"
                )
        elif receipt_class == "SUCCEEDED_NO_EFFECT":
            state = "VERIFIED_COMPLETE"
            reason = "no-effect completion verified against unchanged exact source"
        elif receipt_class == "HELD":
            state = "VERIFIED_HELD"
            reason = "held outcome verified against unchanged exact source"
        else:
            raise ValueError(
                "delivery receipt class is not independently completable"
            )

        store.finalize_verification(
            run_id=run_id,
            subject_id=subject_id,
            expected_receipt_sha256=receipt_sha256,
            state=state,
            verifier=verifier,
            reason=reason,
            now=float(clock()),
        )
        return PortalWaveVerificationResult(
            run_id=run_id,
            subject_id=subject_id,
            state=state,
            result_repository=(
                str(record["result_repository"])
                if record["result_repository"] is not None
                else None
            ),
            result_ref=(
                str(record["result_ref"])
                if record["result_ref"] is not None
                else None
            ),
            result_head=(
                str(record["result_head"])
                if record["result_head"] is not None
                else None
            ),
            reason=reason,
        )
    finally:
        store.close()
