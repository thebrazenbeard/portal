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


def _common(tmp_path: Path) -> dict[str, object]:
    return dict(
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="worklaptop", max_parallel=2),),
        budget=WaveExecutionBudget(2, 2, 2, 2),
        lease_ttl=300.0,
        token=None,
    )


def test_frontier_currentness_can_only_add_generation_exclusions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="d" * 64,
            plan_path=tmp_path / "plan.json",
            assigned=0,
            claimed=0,
            held=0,
            packets=(),
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")

    controller.run(
        session_id="portfolio",
        holder="vera",
        frontier_currentness_provider=lambda wave_path: (
            ("repository", "portal"),
        ),
        **_common(tmp_path),
    )

    assert calls[0]["active_subjects"] == ()
    assert calls[0]["excluded_subjects"] == (("repository", "portal"),)
    controller.close()


def test_frontier_currentness_never_excludes_already_active_subject(
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

    observations = iter(
        (
            (),
            (
                ("repository", "project-runner"),
                ("repository", "portal"),
            ),
        )
    )
    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    common = _common(tmp_path)

    controller.run(
        session_id="portfolio",
        holder="vera",
        frontier_currentness_provider=lambda wave_path: next(observations),
        **common,
    )
    controller.continue_run(
        session_id="portfolio",
        holder="vera",
        frontier_currentness_provider=lambda wave_path: next(observations),
        **common,
    )

    assert calls[1]["active_subjects"] == (
        ("repository", "project-runner"),
    )
    assert calls[1]["excluded_subjects"] == (
        ("repository", "portal"),
    )
    controller.close()


def test_resident_refill_refreshes_frontier_currentness_each_generation(
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

    snapshots = iter(
        (
            (),
            (("repository", "portal"),),
        )
    )
    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)
    monkeypatch.setattr(portal_session, "PortalWaveStore", FakeWaveStore)

    controller = portal_session.PortalCommandSession(tmp_path / "portal.sqlite3")
    controller.run_until_idle(
        session_id="portfolio",
        holder="vera",
        verifier="vera-review",
        max_cycles=2,
        max_idle_cycles=1,
        poll_seconds=0.0,
        frontier_currentness_provider=lambda wave_path: next(snapshots),
        **_common(tmp_path),
    )

    assert calls[0]["excluded_subjects"] == ()
    assert calls[1]["excluded_subjects"] == (
        ("repository", "portal"),
    )
    controller.close()
