from __future__ import annotations

from pathlib import Path

import pytest

import portal.session as portal_session
from portal.adapters import PortalDispatchRecord, PortalRouteBinding
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
