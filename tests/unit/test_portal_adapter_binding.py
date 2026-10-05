from __future__ import annotations

from pathlib import Path

import pytest

import portal.session as portal_session
from portal.adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from portal.models import ExecutionNode
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _packet(run_id: str) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id="project-runner",
        repository="thebrazenbeard/project-runner",
        ref="main",
        exact_head="a" * 40,
        node_id="worklaptop",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id="lineage",
        work_fingerprint="c" * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="advance",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _install_prepare(monkeypatch, tmp_path: Path) -> None:
    def fake_prepare(**kwargs):
        packet = _packet(kwargs["run_id"])
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=1,
            claimed=1,
            held=0,
            packets=(packet,),
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)


def _common(tmp_path: Path) -> dict[str, object]:
    return dict(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        verifier="vera-review",
        max_cycles=1,
        max_idle_cycles=1,
        poll_seconds=0.0,
    )


def test_adapter_route_is_bound_before_dispatch_and_survives_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_prepare(monkeypatch, tmp_path)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")

    class FakeAdapter:
        def select_routes(self, result):
            assert result.wave_run_id == "portal::g1"
            return (
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id="project-runner",
                    adapter_id="executor",
                    route_id="WorkLaptop:g9",
                ),
            )

        def dispatch(self, result, routes):
            status = controller.status("portal")
            subject = status["subjects"][0]
            assert subject["adapter_id"] == "executor"
            assert subject["route_id"] == "WorkLaptop:g9"
            assert subject["dispatch_state"] == "BOUND"
            assert tuple(routes)[0].route_id == "WorkLaptop:g9"
            return (
                PortalDispatchRecord(
                    subject_kind="repository",
                    subject_id="project-runner",
                    adapter_id="executor",
                    route_id="WorkLaptop:g9",
                    state="DISPATCHED",
                    evidence_id="executor:task-123",
                ),
            )

    result = controller.run_until_idle(
        **_common(tmp_path),
        execution_adapter=FakeAdapter(),
    )
    assert result.stop_reason == "MAX_CYCLES"
    status = controller.status("portal")
    subject = status["subjects"][0]
    assert subject["dispatch_state"] == "DISPATCHED"
    assert subject["dispatch_evidence_id"] == "executor:task-123"
    controller.close()

    reopened = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    subject = reopened.status("portal")["subjects"][0]
    assert subject["adapter_id"] == "executor"
    assert subject["route_id"] == "WorkLaptop:g9"
    assert subject["dispatch_state"] == "DISPATCHED"
    reopened.close()


def test_bound_route_cannot_be_substituted_without_reconciliation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_prepare(monkeypatch, tmp_path)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    result = controller.run(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
    )
    controller.bind_routes(
        session_id="portal",
        holder="vera",
        wave_run_id=result.wave_run_id,
        bindings=(
            PortalRouteBinding(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
            ),
        ),
    )

    with pytest.raises(ValueError, match="route already bound"):
        controller.bind_routes(
            session_id="portal",
            holder="vera",
            wave_run_id=result.wave_run_id,
            bindings=(
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id="project-runner",
                    adapter_id="workbridge",
                    route_id="WorkLaptop:bridge",
                ),
            ),
        )
    controller.close()


def test_dispatch_exception_leaves_route_bound_for_reconciliation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_prepare(monkeypatch, tmp_path)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")

    class ExplodingAdapter:
        def select_routes(self, result):
            return (
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id="project-runner",
                    adapter_id="executor",
                    route_id="WorkLaptop:g9",
                ),
            )

        def dispatch(self, result, routes):
            raise RuntimeError("transport disconnected after dispatch boundary")

    with pytest.raises(RuntimeError, match="transport disconnected"):
        controller.run_until_idle(
            **_common(tmp_path),
            execution_adapter=ExplodingAdapter(),
        )

    subject = controller.status("portal")["subjects"][0]
    assert subject["adapter_id"] == "executor"
    assert subject["route_id"] == "WorkLaptop:g9"
    assert subject["dispatch_state"] == "BOUND"
    assert subject["state"] == "ACTIVE"
    controller.close()

def test_adapter_reconciliation_can_verify_completion_and_free_subject(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_prepare(monkeypatch, tmp_path)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    result = controller.run(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
    )
    controller.bind_routes(
        session_id="portal",
        holder="vera",
        wave_run_id=result.wave_run_id,
        bindings=(
            PortalRouteBinding(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
            ),
        ),
    )
    controller.record_dispatches(
        session_id="portal",
        holder="vera",
        records=(
            PortalDispatchRecord(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
                state="DISPATCHED",
                evidence_id="executor:task-123",
            ),
        ),
    )

    from portal.adapters import PortalReconciliationRecord

    controller.record_reconciliations(
        session_id="portal",
        holder="vera",
        records=(
            PortalReconciliationRecord(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
                state="VERIFIED_COMPLETE",
                evidence_id="executor:task-123:verified",
            ),
        ),
    )

    subject = controller.status("portal")["subjects"][0]
    assert subject["state"] == "TERMINAL"
    assert subject["verification_state"] == "VERIFIED_COMPLETE"
    assert subject["dispatch_evidence_id"] == "executor:task-123:verified"
    controller.close()


def test_outcome_unknown_reconciliation_stays_active_and_route_pinned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_prepare(monkeypatch, tmp_path)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    result = controller.run(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
    )
    controller.bind_routes(
        session_id="portal",
        holder="vera",
        wave_run_id=result.wave_run_id,
        bindings=(
            PortalRouteBinding(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
            ),
        ),
    )

    from portal.adapters import PortalReconciliationRecord

    controller.record_reconciliations(
        session_id="portal",
        holder="vera",
        records=(
            PortalReconciliationRecord(
                subject_kind="repository",
                subject_id="project-runner",
                adapter_id="executor",
                route_id="WorkLaptop:g9",
                state="OUTCOME_UNKNOWN",
                evidence_id="executor:ambiguous-123",
            ),
        ),
    )

    subject = controller.status("portal")["subjects"][0]
    assert subject["state"] == "ACTIVE"
    assert subject["verification_state"] == "OUTCOME_UNKNOWN"
    assert subject["adapter_id"] == "executor"
    assert subject["route_id"] == "WorkLaptop:g9"

    with pytest.raises(ValueError, match="route already bound"):
        controller.bind_routes(
            session_id="portal",
            holder="vera",
            wave_run_id=result.wave_run_id,
            bindings=(
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id="project-runner",
                    adapter_id="workbridge",
                    route_id="WorkLaptop:bridge",
                ),
            ),
        )
    controller.close()

def test_refill_uses_adapter_reconciliation_to_free_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        if len(prepare_calls) == 1:
            packet = _packet(kwargs["run_id"])
        elif len(prepare_calls) == 2:
            packet = PortalWavePacket(
                **{
                    **_packet(kwargs["run_id"]).__dict__,
                    "subject_id": "lou-pole",
                    "repository": "thebrazenbeard/lou-pole",
                    "lineage_id": "lineage-lou-pole",
                }
            )
        else:
            return PortalWavePreparationResult(
                run_id=kwargs["run_id"],
                plan_sha256="d" * 64,
                plan_path=tmp_path / f"{kwargs['run_id']}.json",
                assigned=0,
                claimed=0,
                held=0,
                packets=(),
            )
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=1,
            claimed=1,
            held=0,
            packets=(packet,),
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    class FakeWaveStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_delivery(self, *, run_id, subject_id):
            return {"state": "PENDING"}

    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)

    class ReconcilingAdapter:
        def __init__(self):
            self.dispatched: list[str] = []
            self.reconciled = False

        def select_routes(self, result):
            return tuple(
                PortalRouteBinding(
                    subject_kind="repository",
                    subject_id=packet.subject_id,
                    adapter_id="executor",
                    route_id=f"WorkLaptop:g9:{packet.subject_id}",
                )
                for packet in result.packets
            )

        def dispatch(self, result, routes):
            self.dispatched.extend(binding.subject_id for binding in routes)
            return tuple(
                PortalDispatchRecord(
                    subject_kind=binding.subject_kind,
                    subject_id=binding.subject_id,
                    adapter_id=binding.adapter_id,
                    route_id=binding.route_id,
                    state="DISPATCHED",
                    evidence_id=f"executor:{binding.subject_id}:task",
                )
                for binding in routes
            )

        def reconcile(self, status):
            if self.reconciled:
                return ()
            subject = next(
                item
                for item in status["subjects"]
                if item["subject_id"] == "project-runner"
            )
            self.reconciled = True
            return (
                PortalReconciliationRecord(
                    subject_kind=subject["subject_kind"],
                    subject_id=subject["subject_id"],
                    adapter_id=subject["adapter_id"],
                    route_id=subject["route_id"],
                    state="VERIFIED_COMPLETE",
                    evidence_id="executor:project-runner:verified",
                ),
            )

    adapter = ReconcilingAdapter()
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    result = controller.run_until_idle(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        verifier="vera-review",
        max_cycles=2,
        max_idle_cycles=1,
        poll_seconds=0.0,
        execution_adapter=adapter,
    )

    assert adapter.dispatched == ["project-runner", "lou-pole"]
    assert prepare_calls[1]["active_subjects"] == ()
    assert prepare_calls[1]["excluded_subjects"] == (
        ("repository", "project-runner"),
    )
    assert result.summary == {"active": 1, "held": 0, "terminal": 1}
    controller.close()

