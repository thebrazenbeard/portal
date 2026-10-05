from __future__ import annotations

from pathlib import Path
import sys

import portal.process_adapter as process_adapter
import portal.wave_runtime as wave_runtime
import portal.worker_runtime as worker_runtime
from portal.adapters import PortalRouteBinding
from portal.models import ExecutionNode
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket, PortalWaveStore
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_runtime import PortalWorkerPassResult, PortalWorkerSlotResult


def _packet(
    *,
    run_id: str = "portal::g1",
    subject_id: str = "alpha",
    node_id: str = "worklaptop",
) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=f"thebrazenbeard/{subject_id}",
        ref="main",
        exact_head="a" * 40,
        node_id=node_id,
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage:{subject_id}",
        work_fingerprint="c" * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="advance",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _result(*packets: PortalWavePacket) -> PortalSessionResult:
    return PortalSessionResult(
        session_id="portal",
        control_state="RUNNING",
        generation=1,
        wave_run_id="portal::g1",
        packets=packets,
        summary={"active": len(packets), "held": 0, "terminal": 0},
    )


def _adapter(tmp_path: Path) -> process_adapter.PortalProposalProcessAdapter:
    return process_adapter.PortalProposalProcessAdapter(
        state_db=tmp_path / "portal.sqlite3",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        backends={
            "worklaptop": ProcessWorkerSpec(
                command=(sys.executable,),
                timeout_seconds=10.0,
            )
        },
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=300.0,
        token=None,
    )


def test_process_dispatch_scopes_worker_claims_to_bound_subjects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return PortalWorkerPassResult(
            run_id="portal::g1",
            slots=(
                PortalWorkerSlotResult(
                    node_id="worklaptop",
                    slot=0,
                    holder="portal:worklaptop:0",
                    subject_id="alpha",
                    claimed=True,
                    state="AWAITING_PROMOTION",
                    receipt_class="PROPOSED_SOURCE_TREE",
                    reason="proposal persisted",
                ),
            ),
            claimed=1,
            verified_complete=0,
            verified_held=0,
            failed_or_unknown=0,
            no_work=0,
            awaiting_promotion=1,
        )

    class FakeStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_source_proposal(self, *, run_id, subject_id):
            assert subject_id == "alpha"
            return {
                "state": "AWAITING_PROMOTION",
                "execution_request_sha256": "d" * 64,
            }

    monkeypatch.setattr(process_adapter, "run_wave_proposal_workers_once", fake_run)
    monkeypatch.setattr(process_adapter, "PortalWaveStore", FakeStore)

    adapter = _adapter(tmp_path)
    result = _result(_packet(subject_id="alpha"), _packet(subject_id="beta"))
    records = tuple(
        adapter.dispatch(
            result,
            (
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id="alpha",
                    adapter_id="process-proposal",
                    route_id="process-proposal:worklaptop",
                ),
            ),
        )
    )

    assert calls[0]["allowed_subject_ids"] == ("alpha",)
    assert [record.subject_id for record in records] == ["alpha"]


def test_process_reconcile_accepts_router_filtered_subject_subset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_source_proposal(self, *, run_id, subject_id):
            return {
                "state": "AWAITING_PROMOTION",
                "execution_request_sha256": ("a" if subject_id == "alpha" else "b") * 64,
            }

    monkeypatch.setattr(process_adapter, "PortalWaveStore", FakeStore)
    adapter = _adapter(tmp_path)

    subjects = [
        {
            "subject_kind": "repository",
            "subject_id": "alpha",
            "state": "ACTIVE",
            "wave_run_id": "portal::g1",
            "adapter_id": "process-proposal",
            "route_id": "process-proposal:worklaptop",
        },
        {
            "subject_kind": "repository",
            "subject_id": "beta",
            "state": "ACTIVE",
            "wave_run_id": "portal::g1",
            "adapter_id": "process-proposal",
            "route_id": "process-proposal:worklaptop",
        },
    ]
    records = tuple(
        adapter.reconcile(
            {"subjects": subjects},
            (subjects[1],),
        )
    )

    assert [record.subject_id for record in records] == ["beta"]
    assert records[0].evidence_id == "proposal:" + ("b" * 64)


def test_worker_slot_forwards_subject_allowlist_to_wave_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def claim_advisory_delivery(self, **kwargs):
            calls.append(kwargs)
            return None

    monkeypatch.setattr(worker_runtime, "PortalWaveStore", FakeStore)

    result = worker_runtime._run_proposal_slot(
        state_db=tmp_path / "portal.sqlite3",
        run_id="portal::g1",
        node_id="worklaptop",
        slot=0,
        backend=ProcessWorkerSpec(
            command=(sys.executable,),
            timeout_seconds=10.0,
        ),
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=300.0,
        token=None,
        transport=None,
        clock=lambda: 10.0,
        claim_attempts=1,
        allowed_subject_ids=("beta",),
    )

    assert result.state == "NO_WORK"
    assert calls[0]["allowed_subject_ids"] == ("beta",)


def _seed_packet(
    store: PortalWaveStore,
    *,
    run_id: str,
    subject_id: str,
    delivery_state: str,
    lease_expires_at: float | None,
) -> None:
    store.connection.execute(
        """
        INSERT INTO portal_wave_packets(
            run_id, subject_id, repository, ref, exact_head, node_id, lane_id,
            state, plan_sha256, fencing_token, lineage_id, work_fingerprint,
            action, effect_ceiling, review_gate, frontier, lead_identity,
            reviewer_identities_json, reason, created_at, updated_at
        ) VALUES (?, ?, ?, 'main', ?, 'worklaptop', 'vera', 'CLAIMED', ?,
                  1, ?, ?, 'ADVANCE', 'SOURCE_ONLY', 'EXACT_HEAD_REVIEW',
                  'advance', 'vera', '[]', NULL, 0.0, 0.0)
        """,
        (
            run_id,
            subject_id,
            f"thebrazenbeard/{subject_id}",
            "a" * 40,
            "b" * 64,
            f"lineage:{subject_id}",
            "c" * 64,
        ),
    )
    store.connection.execute(
        """
        INSERT INTO portal_wave_deliveries(
            run_id, subject_id, node_id, state, holder, fencing_token,
            lease_expires_at, created_at, updated_at
        ) VALUES (?, ?, 'worklaptop', ?, ?, ?, ?, 0.0, 0.0)
        """,
        (
            run_id,
            subject_id,
            delivery_state,
            "other-driver" if delivery_state == "DELIVERED" else None,
            1 if delivery_state == "DELIVERED" else 0,
            lease_expires_at,
        ),
    )


def test_advisory_claim_allowlist_does_not_touch_other_same_node_delivery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = PortalWaveStore(tmp_path / "portal.sqlite3")
    store.ensure_run(
        run_id="run",
        config_digest="d" * 64,
        wave_sha256="e" * 64,
        plan_sha256="f" * 64,
        holder="portal",
        now=0.0,
    )
    _seed_packet(
        store,
        run_id="run",
        subject_id="alpha",
        delivery_state="DELIVERED",
        lease_expires_at=5.0,
    )
    _seed_packet(
        store,
        run_id="run",
        subject_id="beta",
        delivery_state="PENDING",
        lease_expires_at=None,
    )
    monkeypatch.setattr(wave_runtime, "_require_advisory_claim", lambda **kwargs: None)

    claim = store.claim_advisory_delivery(
        run_id="run",
        node_id="worklaptop",
        holder="process-proposal:0",
        now=10.0,
        ttl=30.0,
        allowed_subject_ids=("beta",),
    )

    assert claim is not None
    assert claim.subject_id == "beta"
    alpha_state = store.connection.execute(
        """
        SELECT state
        FROM portal_wave_deliveries
        WHERE run_id = 'run' AND subject_id = 'alpha'
        """
    ).fetchone()[0]
    assert alpha_state == "DELIVERED"
    store.close()
