from __future__ import annotations

from pathlib import Path
import sys

import pytest

import portal.process_adapter as process_adapter
from portal.adapters import PortalRouteBinding
from portal.models import ExecutionNode
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_runtime import PortalWorkerPassResult, PortalWorkerSlotResult


def _packet(
    *,
    run_id: str = "portal::g1",
    subject_id: str = "project-runner",
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


def test_select_routes_binds_packet_to_assigned_process_node(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    bindings = tuple(adapter.select_routes(_result(_packet())))

    assert bindings == (
        PortalRouteBinding(
            subject_kind="repository",
            subject_id="project-runner",
            adapter_id="process-proposal",
            route_id="process-proposal:worklaptop",
        ),
    )


def test_select_routes_fails_closed_when_assigned_node_has_no_backend(
    tmp_path: Path,
) -> None:
    adapter = process_adapter.PortalProposalProcessAdapter(
        state_db=tmp_path / "portal.sqlite3",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        backends={},
        workspace_root=tmp_path / "workers",
        holder_prefix="portal",
        delivery_lease_ttl=300.0,
        token=None,
    )

    with pytest.raises(ValueError, match="missing process proposal backend"):
        tuple(adapter.select_routes(_result(_packet())))


def test_dispatch_runs_existing_proposal_worker_runtime_and_records_digest(
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
                    subject_id="project-runner",
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
            return {
                "state": "AWAITING_PROMOTION",
                "execution_request_sha256": "d" * 64,
            }

    monkeypatch.setattr(process_adapter, "run_wave_proposal_workers_once", fake_run)
    monkeypatch.setattr(process_adapter, "PortalWaveStore", FakeStore)

    adapter = _adapter(tmp_path)
    result = _result(_packet())
    bindings = tuple(adapter.select_routes(result))
    records = tuple(adapter.dispatch(result, bindings))

    assert len(calls) == 1
    assert calls[0]["run_id"] == "portal::g1"
    assert records[0].state == "PROPOSAL_READY"
    assert records[0].evidence_id == "proposal:" + ("d" * 64)


def test_reconcile_holds_durable_proposal_awaiting_promotion(
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
                "execution_request_sha256": "e" * 64,
            }

    monkeypatch.setattr(process_adapter, "PortalWaveStore", FakeStore)

    adapter = _adapter(tmp_path)
    records = tuple(
        adapter.reconcile(
            {
                "subjects": [
                    {
                        "subject_kind": "repository",
                        "subject_id": "project-runner",
                        "state": "ACTIVE",
                        "wave_run_id": "portal::g1",
                        "adapter_id": "process-proposal",
                        "route_id": "process-proposal:worklaptop",
                    }
                ]
            }
        )
    )

    assert records[0].state == "VERIFIED_HELD"
    assert records[0].evidence_id == "proposal:" + ("e" * 64)


def test_reconcile_preserves_ambiguous_worker_outcome(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_source_proposal(self, *, run_id, subject_id):
            raise ValueError("no proposal")

        def load_delivery(self, *, run_id, subject_id):
            return {
                "state": "OUTCOME_UNKNOWN",
                "evidence_sha256": "f" * 64,
                "receipt_sha256": None,
                "receipt_class": "OUTCOME_UNKNOWN",
            }

    monkeypatch.setattr(process_adapter, "PortalWaveStore", FakeStore)

    adapter = _adapter(tmp_path)
    records = tuple(
        adapter.reconcile(
            {
                "subjects": [
                    {
                        "subject_kind": "repository",
                        "subject_id": "project-runner",
                        "state": "ACTIVE",
                        "wave_run_id": "portal::g1",
                        "adapter_id": "process-proposal",
                        "route_id": "process-proposal:worklaptop",
                    }
                ]
            }
        )
    )

    assert records[0].state == "OUTCOME_UNKNOWN"
    assert records[0].evidence_id == "delivery:" + ("f" * 64)
