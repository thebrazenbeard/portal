from __future__ import annotations

from pathlib import Path

import portal.session as portal_session
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


def test_run_persists_active_subject_and_continue_refills_around_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        packets = (_packet(kwargs["run_id"]),) if len(calls) == 1 else ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    budget = WaveExecutionBudget(
        max_parallel=2,
        max_per_identity=2,
        max_per_family=2,
        max_per_lane=2,
    )
    common = dict(
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=2),),
        budget=budget,
        lease_ttl=300.0,
        token=None,
    )

    first = controller.run(session_id="portal", holder="vera", **common)
    second = controller.continue_run(session_id="portal", holder="vera", **common)

    assert first.generation == 1
    assert first.summary["active"] == 1
    assert calls[0]["active_subjects"] == ()
    assert calls[0]["excluded_subjects"] == ()
    assert calls[1]["active_subjects"] == (("repository", "project-runner"),)
    assert second.generation == 2
    assert second.summary["active"] == 1
    controller.close()


import pytest

from portal.wave_runtime import PortalWaveVerificationResult


def test_hold_is_durable_and_excludes_subject_from_later_admission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        packets = (_packet(kwargs["run_id"]),) if len(calls) == 1 else ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    budget = WaveExecutionBudget(2, 2, 2, 2)
    common = dict(
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=2),),
        budget=budget,
        lease_ttl=300.0,
        token=None,
    )

    controller.run(session_id="portal", holder="vera", **common)
    held = controller.hold(
        session_id="portal",
        holder="vera",
        subject_kind="repository",
        subject_id="project-runner",
    )
    assert held["summary"] == {"active": 1, "held": 0, "terminal": 0}
    assert held["subjects"][0]["hold_requested"] is True

    controller.continue_run(session_id="portal", holder="vera", **common)
    assert calls[1]["active_subjects"] == (("repository", "project-runner"),)
    assert calls[1]["excluded_subjects"] == ()
    controller.close()


def test_stop_preserves_active_work_and_blocks_continue_until_run_restarts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        packets = (_packet(kwargs["run_id"]),) if len(calls) == 1 else ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    budget = WaveExecutionBudget(2, 2, 2, 2)
    common = dict(
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=2),),
        budget=budget,
        lease_ttl=300.0,
        token=None,
    )

    controller.run(session_id="portal", holder="vera", **common)
    stopped = controller.stop(session_id="portal", holder="vera")
    assert stopped["control_state"] == "STOPPED"
    assert stopped["summary"]["active"] == 1

    with pytest.raises(ValueError, match="stopped"):
        controller.continue_run(session_id="portal", holder="vera", **common)

    restarted = controller.run(session_id="portal", holder="vera", **common)
    assert restarted.control_state == "RUNNING"
    assert restarted.generation == 2
    assert calls[1]["active_subjects"] == (("repository", "project-runner"),)
    controller.close()


def test_complete_requires_verified_complete_evidence_before_terminal_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    controller.run(
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

    def stale(**kwargs):
        return PortalWaveVerificationResult(
            run_id=kwargs["run_id"],
            subject_id=kwargs["subject_id"],
            state="VERIFICATION_STALE",
            result_repository=None,
            result_ref=None,
            result_head=None,
            reason="stale",
        )

    monkeypatch.setattr(portal_session, "verify_portal_wave_delivery", stale)
    with pytest.raises(ValueError, match="not verified complete"):
        controller.complete(
            session_id="portal",
            holder="vera",
            subject_kind="repository",
            subject_id="project-runner",
            verifier="vera-review",
            token=None,
        )
    assert controller.status("portal")["summary"]["active"] == 1

    def verified(**kwargs):
        return PortalWaveVerificationResult(
            run_id=kwargs["run_id"],
            subject_id=kwargs["subject_id"],
            state="VERIFIED_COMPLETE",
            result_repository=None,
            result_ref=None,
            result_head=None,
            reason="verified",
        )

    monkeypatch.setattr(portal_session, "verify_portal_wave_delivery", verified)
    done = controller.complete(
        session_id="portal",
        holder="vera",
        subject_kind="repository",
        subject_id="project-runner",
        verifier="vera-review",
        token=None,
    )
    assert done["summary"] == {"active": 0, "held": 0, "terminal": 1}

    controller.close()
    reopened = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    restored = reopened.status("portal")
    assert restored["control_state"] == "RUNNING"
    assert restored["generation"] == 1
    assert restored["summary"]["terminal"] == 1
    assert restored["subjects"][0]["verification_state"] == "VERIFIED_COMPLETE"
    reopened.close()

def _packet_for(run_id: str, subject_id: str, repository: str) -> PortalWavePacket:
    packet = _packet(run_id)
    return PortalWavePacket(
        run_id=packet.run_id,
        subject_id=subject_id,
        repository=repository,
        ref=packet.ref,
        exact_head=packet.exact_head,
        node_id=packet.node_id,
        lane_id=packet.lane_id,
        state=packet.state,
        plan_sha256=packet.plan_sha256,
        fencing_token=packet.fencing_token,
        lineage_id=f"{packet.lineage_id}:{subject_id}",
        work_fingerprint=packet.work_fingerprint,
        action=packet.action,
        effect_ceiling=packet.effect_ceiling,
        review_gate=packet.review_gate,
        frontier=packet.frontier,
        lead_identity=packet.lead_identity,
        reviewer_identities=packet.reviewer_identities,
    )


def test_refill_loop_frees_verified_completion_and_admits_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        index = len(prepare_calls)
        if index == 1:
            packets = (
                _packet_for(
                    kwargs["run_id"],
                    "project-runner",
                    "thebrazenbeard/project-runner",
                ),
            )
        elif index == 2:
            packets = (
                _packet_for(
                    kwargs["run_id"],
                    "lou-pole",
                    "thebrazenbeard/lou-pole",
                ),
            )
        else:
            packets = ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    class FakeWaveStore:
        def __init__(self, path):
            self.path = path

        def close(self):
            pass

        def load_delivery(self, *, run_id, subject_id):
            if subject_id == "project-runner":
                return {"state": "RECEIPT_RECORDED"}
            return {"state": "PENDING"}

    def fake_verify(**kwargs):
        return PortalWaveVerificationResult(
            run_id=kwargs["run_id"],
            subject_id=kwargs["subject_id"],
            state="VERIFIED_COMPLETE",
            result_repository=None,
            result_ref=None,
            result_head=None,
            reason="verified",
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)
    monkeypatch.setattr(portal_session, "verify_portal_wave_delivery", fake_verify)

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
        max_cycles=4,
        max_idle_cycles=1,
        poll_seconds=0.0,
    )

    assert result.stop_reason == "WAITING_ACTIVE"
    assert prepare_calls[1]["active_subjects"] == ()
    assert prepare_calls[1]["excluded_subjects"] == (
        ("repository", "project-runner"),
    )
    assert prepare_calls[2]["active_subjects"] == (
        ("repository", "lou-pole"),
    )
    assert prepare_calls[2]["excluded_subjects"] == (
        ("repository", "project-runner"),
    )
    status = controller.status("portal")
    assert status["summary"] == {"active": 1, "held": 0, "terminal": 1}
    controller.close()


def test_reconcile_keeps_outcome_unknown_active_and_never_replays_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    class FakeWaveStore:
        def __init__(self, path):
            self.path = path

        def close(self):
            pass

        def load_delivery(self, *, run_id, subject_id):
            return {"state": "OUTCOME_UNKNOWN"}

    called = {"verify": False}

    def should_not_verify(**kwargs):
        called["verify"] = True
        raise AssertionError("OUTCOME_UNKNOWN must not be replayed or completed")

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)
    monkeypatch.setattr(
        portal_session,
        "verify_portal_wave_delivery",
        should_not_verify,
    )

    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    controller.run(
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
    reconciled = controller.reconcile_active(
        session_id="portal",
        holder="vera",
        verifier="vera-review",
        token=None,
    )

    assert reconciled["changed"] == 0
    assert reconciled["unresolved"] == 1
    assert called["verify"] is False
    status = controller.status("portal")
    assert status["summary"]["active"] == 1
    assert status["subjects"][0]["verification_state"] == "OUTCOME_UNKNOWN"
    controller.close()

def test_refill_refreshes_live_node_occupancy_each_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        packets = (_packet(kwargs["run_id"]),) if len(calls) == 1 else ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    class FakeWaveStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_delivery(self, *, run_id, subject_id):
            return {"state": "PENDING"}

    snapshots = iter((
        {"lappy": 54, "worklaptop": 0},
        {"lappy": 53, "worklaptop": 1},
    ))

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)

    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    controller.run_until_idle(
        session_id="portal",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(
            ExecutionNode(node_id="lappy", max_parallel=8),
            ExecutionNode(node_id="worklaptop", max_parallel=8),
        ),
        budget=WaveExecutionBudget(8, 8, 8, 8),
        lease_ttl=300.0,
        token=None,
        verifier="vera-review",
        max_cycles=2,
        max_idle_cycles=1,
        node_occupancy_provider=lambda: next(snapshots),
    )

    assert calls[0]["occupied_node_slots"] == {
        "lappy": 54,
        "worklaptop": 0,
    }
    assert calls[1]["occupied_node_slots"] == {
        "lappy": 53,
        "worklaptop": 1,
    }
    controller.close()

def test_unbounded_refill_waits_through_quiet_cycle_until_explicit_stop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        prepare_calls.append(kwargs)
        packets = (_packet(kwargs["run_id"]),) if len(prepare_calls) == 1 else ()
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    class FakeWaveStore:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def load_delivery(self, *, run_id, subject_id):
            return {"state": "PENDING"}

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)

    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    stopper = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    sleeps: list[float] = []

    def stop_during_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        stopper.stop(session_id="portal", holder="vera")

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
        max_cycles=0,
        max_idle_cycles=0,
        poll_seconds=0.25,
        sleep=stop_during_sleep,
    )

    assert result.stop_reason == "STOPPED"
    assert sleeps == [0.25]
    assert len(prepare_calls) == 2
    assert result.summary["active"] == 1
    stopper.close()
    controller.close()

