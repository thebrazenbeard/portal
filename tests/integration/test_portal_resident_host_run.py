from __future__ import annotations

from pathlib import Path

import portal.session as portal_session
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.host_pump import PortalHostDriverResult, PortalHostPump
from portal.models import ExecutionNode
from portal.route_resolver import PortalRouteAdvertisement
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _packet(run_id: str, subject_id: str) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=f"thebrazenbeard/{subject_id}",
        ref="main",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=f"lineage:{subject_id}",
        work_fingerprint=("c" if subject_id == "alpha" else "d") * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=f"Advance {subject_id}.",
        lead_identity="vera",
        reviewer_identities=(),
    )


def test_resident_between_cycle_hook_pumps_and_refills_with_zero_poll_delay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_calls: list[dict[str, object]] = []
    events: list[str] = []

    def fake_prepare(**kwargs):
        events.append("prepare")
        prepare_calls.append(kwargs)
        excluded = set(kwargs["excluded_subjects"])
        active = set(kwargs["active_subjects"])
        packets: tuple[PortalWavePacket, ...] = ()
        if not active:
            if ("repository", "alpha") not in excluded:
                packets = (_packet(kwargs["run_id"], "alpha"),)
            elif ("repository", "beta") not in excluded:
                packets = (_packet(kwargs["run_id"], "beta"),)
        return PortalWavePreparationResult(
            run_id=kwargs["run_id"],
            plan_sha256="e" * 64,
            plan_path=tmp_path / f"{kwargs['run_id']}.json",
            assigned=len(packets),
            claimed=len(packets),
            held=0,
            packets=packets,
        )

    monkeypatch.setattr(portal_session, "prepare_portal_wave", fake_prepare)

    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    for subject_id in ("alpha", "beta"):
        store.advertise_route(
            PortalRouteAdvertisement(
                adapter_id="github",
                route_id=f"github:{subject_id}",
                node_id="repo-native",
                target_kind="repository",
                target_id=f"thebrazenbeard/{subject_id}",
                capabilities=("semantic_work",),
                effect_capabilities=("SOURCE_ONLY",),
                authorized_effects=("SOURCE_ONLY",),
                available=True,
                attached=True,
                current=True,
                preference=50,
            ),
            observed_at=0.0,
            ttl_seconds=1_000_000_000_000.0,
        )

    adapter = PortalHostExecutionAdapter(store=store)
    executed: list[str] = []

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            subject_id = str(dispatch["subject_id"])
            executed.append(subject_id)
            return PortalHostDriverResult(
                state="VERIFIED_COMPLETE",
                evidence_id=f"github:{subject_id}:verified",
            )

    pump = PortalHostPump(
        store=store,
        drivers={"github": Driver()},
        attempt_id_factory=lambda dispatch: (
            "attempt:" + str(dispatch["subject_id"])
        ),
    )

    def before_cycle():
        events.append("refresh")

    def between_cycles():
        events.append("pump")
        return pump.run_once(session_id="portfolio")

    controller = portal_session.PortalCommandSession(state_db)
    result = controller.run_until_idle(
        session_id="portfolio",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="repo-native", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        verifier="vera-review",
        max_cycles=6,
        max_idle_cycles=0,
        poll_seconds=0.0,
        execution_adapter=adapter,
        before_cycle=before_cycle,
        between_cycles=between_cycles,
    )

    assert events == [
        "refresh", "prepare",
        "refresh", "pump", "prepare",
        "refresh", "pump", "prepare",
    ]
    assert executed == ["alpha", "beta"]
    assert result.stop_reason == "IDLE"
    assert result.summary == {"active": 0, "held": 0, "terminal": 2}
    assert store.pending_dispatches(session_id="portfolio") == ()
    assert store.unresolved_dispatches(session_id="portfolio") == ()
    assert prepare_calls[1]["active_subjects"] == ()
    assert prepare_calls[1]["excluded_subjects"] == (
        ("repository", "alpha"),
    )
    assert prepare_calls[2]["excluded_subjects"] == (
        ("repository", "alpha"),
        ("repository", "beta"),
    )

    controller.close()
    store.close()

def test_cli_run_with_host_drivers_pumps_between_refill_generations(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import json
    from types import SimpleNamespace

    import portal.cli as portal_cli

    calls: list[tuple[str, object]] = []

    class FakeSession:
        def __init__(self, path):
            calls.append(("session_path", path))

        def close(self):
            pass

        def run_until_idle(self, **kwargs):
            calls.append(("run_until_idle", kwargs))
            between_cycles = kwargs.get("between_cycles")
            assert callable(between_cycles)
            between_cycles()
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                control_state="RUNNING",
                cycles=(),
                stop_reason="MAX_CYCLES",
                idle_cycles=0,
                summary={"active": 1, "held": 0, "terminal": 0},
            )

        def save_resume_spec(self, **kwargs):
            calls.append(("save_resume_spec", kwargs))

    class FakePump:
        def __init__(self, *, store, drivers):
            calls.append(("pump_init", tuple(sorted(drivers))))

        def run_once(self, *, session_id=None, max_dispatches=None):
            calls.append(("pump", (session_id, max_dispatches)))
            return SimpleNamespace()

    drivers_path = tmp_path / "drivers.yaml"
    drivers_path.write_text("unused by patched loader\n", encoding="utf-8")

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(
        portal_cli,
        "load_host_command_drivers",
        lambda path: {"github": object()},
    )
    monkeypatch.setattr(portal_cli, "PortalHostPump", FakePump)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(
            Path(__file__).resolve().parents[2]
            / "tests" / "fixtures" / "portal-nodes-valid.yaml"
        ),
        "--static-projects",
        "--host-bridge",
        "--host-drivers", str(drivers_path),
        "--host-max-dispatches", "3",
        "--max-cycles", "2",
        "--poll-seconds", "0",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"
    assert ("pump_init", ("github",)) in calls
    assert ("pump", ("portfolio", 3)) in calls

    run_kwargs = next(
        value for name, value in calls if name == "run_until_idle"
    )
    assert callable(run_kwargs["between_cycles"])

    save_kwargs = next(
        value for name, value in calls if name == "save_resume_spec"
    )
    assert save_kwargs["spec"]["host_drivers"] == str(drivers_path)
    assert save_kwargs["spec"]["host_max_dispatches"] == 3



def test_cli_resident_run_refreshes_host_probes_before_generation_and_persists_spec(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import json
    import sys
    from types import SimpleNamespace

    import portal.cli as portal_cli

    calls: list[tuple[str, object]] = []
    state_db = tmp_path / "portal.sqlite3"

    probe_script = tmp_path / "probe.py"
    probe_script.write_text(
        "import json\n"
        "print(json.dumps({"
        "'schema':'PORTAL_HOST_PROBE_RESULT_V1',"
        "'adapter_id':'probe',"
        "'evidence_id':'probe:1',"
        "'routes':[{'route_id':'probe:portal','node_id':'lappy',"
        "'target_kind':'repository','target_id':'thebrazenbeard/portal',"
        "'capabilities':['semantic_work'],"
        "'effect_capabilities':['SOURCE_ONLY'],"
        "'available':True,'attached':True,'current':True,'preference':5}],"
        "'occupancy':[]"
        "}))\n",
        encoding="utf-8",
    )
    probes = tmp_path / "probes.yaml"
    probes.write_text(
        "schema: PORTAL_HOST_COMMAND_PROBES_V1\n"
        "probes:\n"
        "  - adapter_id: probe\n"
        f"    command: [{json.dumps(sys.executable)}, {json.dumps(str(probe_script))}]\n",
        encoding="utf-8",
    )
    authority = tmp_path / "authority.yaml"
    authority.write_text(
        "schema: PORTAL_HOST_AUTHORITY_V1\n"
        "grants: []\n",
        encoding="utf-8",
    )

    class FakeSession:
        def __init__(self, path):
            calls.append(("session_path", path))

        def close(self):
            pass

        def run_until_idle(self, **kwargs):
            calls.append(("run_until_idle", kwargs))
            before_cycle = kwargs.get("before_cycle")
            assert callable(before_cycle)
            before_cycle()
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                control_state="RUNNING",
                cycles=(),
                stop_reason="MAX_CYCLES",
                idle_cycles=0,
                summary={"active": 0, "held": 0, "terminal": 0},
            )

        def save_resume_spec(self, **kwargs):
            calls.append(("save_resume_spec", kwargs))

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--state-db", str(state_db),
        "--nodes", str(
            Path(__file__).resolve().parents[2]
            / "tests" / "fixtures" / "portal-nodes-valid.yaml"
        ),
        "--static-projects",
        "--host-bridge",
        "--host-probes", str(probes),
        "--host-authority", str(authority),
        "--host-refresh-ttl-seconds", "120",
        "--max-cycles", "1",
        "--poll-seconds", "0",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"

    store = PortalHostBridgeStore(state_db)
    routes = store.active_routes()
    store.close()
    assert [(item.adapter_id, item.authorized_effects) for item in routes] == [
        ("probe", ())
    ]

    save_kwargs = next(
        value for name, value in calls if name == "save_resume_spec"
    )
    assert save_kwargs["spec"]["host_probes"] == str(probes)
    assert save_kwargs["spec"]["host_authority"] == str(authority)
    assert save_kwargs["spec"]["host_refresh_ttl_seconds"] == 120.0


def test_cli_continue_refreshes_host_state_before_pumping_bound_work(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import json
    from types import SimpleNamespace

    import portal.cli as portal_cli

    events: list[str] = []

    class FakeSession:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def continue_run(self, **kwargs):
            events.append("continue")
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                control_state="RUNNING",
                generation=2,
                wave_run_id="portfolio::g2",
                packets=(),
                summary={"active": 0, "held": 0, "terminal": 1},
            )

        def save_resume_spec(self, **kwargs):
            pass

    class FakePump:
        def run_once(self, *, session_id=None, max_dispatches=None):
            events.append("pump")
            return SimpleNamespace()

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(
        portal_cli,
        "load_execution_nodes",
        lambda path: (ExecutionNode(node_id="lappy", max_parallel=1),),
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_portfolio_paths",
        lambda args: (
            tmp_path / "wave.json",
            tmp_path / "corpus.json",
            tmp_path / "projects.yaml",
        ),
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_execution_adapter",
        lambda args, nodes: object(),
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_host_pump",
        lambda args, adapter: FakePump(),
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_host_refresh",
        lambda args: (lambda: events.append("refresh")),
        raising=False,
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_occupancy",
        lambda args, nodes: ({}, None),
    )
    monkeypatch.setattr(
        portal_cli,
        "_session_frontier_currentness",
        lambda args: None,
    )

    code = portal_cli.entrypoint([
        "continue",
        "--session-id", "portfolio",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--nodes", str(tmp_path / "nodes.yaml"),
        "--static-projects",
        "--host-bridge",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == (
        "PORTAL_COMMAND_SESSION_CONTINUE_V1"
    )
    assert events[:3] == ["refresh", "pump", "continue"]
