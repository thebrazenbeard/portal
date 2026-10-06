from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import portal.cli as portal_cli
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


ROOT = Path(__file__).resolve().parents[2]


def _packet() -> PortalWavePacket:
    return PortalWavePacket(
        run_id="portfolio::g1",
        subject_id="portal",
        repository="thebrazenbeard/portal",
        ref="work/portal-coordinator-v1",
        exact_head="a" * 40,
        node_id="repo-native",
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id="lineage",
        work_fingerprint="c" * 64,
        action="EXECUTE_FRONTIER",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="Advance the bounded frontier.",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _queue_host_dispatch(state_db: Path) -> str:
    store = PortalHostBridgeStore(state_db)
    route = PortalRouteAdvertisement(
        adapter_id="github",
        route_id="repo-native",
        node_id="repo-native",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        capabilities=("semantic_work",),
        effect_capabilities=("SOURCE_ONLY",),
        authorized_effects=("SOURCE_ONLY",),
        available=True,
        attached=True,
        current=True,
        preference=50,
    )
    store.advertise_route(route, ttl_seconds=300.0)
    adapter = PortalHostExecutionAdapter(store=store)
    result = PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id="portfolio::g1",
        packets=(_packet(),),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    binding = adapter.select_routes(result)
    record = adapter.dispatch(result, binding)[0]
    dispatch_id = record.evidence_id.removeprefix("host-dispatch:")
    store.close()
    return dispatch_id


def test_host_advertise_and_routes_cli_round_trip(tmp_path, capsys) -> None:
    state_db = tmp_path / "portal.sqlite3"
    code = portal_cli.entrypoint([
        "host", "advertise",
        "--state-db", str(state_db),
        "--adapter-id", "github",
        "--route-id", "repo-native",
        "--node-id", "repo-native",
        "--target-id", "thebrazenbeard/portal",
        "--capability", "semantic_work",
        "--effect-capability", "SOURCE_ONLY",
        "--authorized-effect", "SOURCE_ONLY",
        "--preference", "50",
        "--ttl-seconds", "300",
    ])
    assert code == 0
    advertised = json.loads(capsys.readouterr().out)
    assert advertised["mode"] == "PORTAL_HOST_ROUTE_ADVERTISE_V1"
    assert advertised["route"]["adapter_id"] == "github"

    code = portal_cli.entrypoint([
        "host", "routes",
        "--state-db", str(state_db),
    ])
    assert code == 0
    routes = json.loads(capsys.readouterr().out)
    assert routes["mode"] == "PORTAL_HOST_ROUTES_V1"
    assert len(routes["routes"]) == 1
    assert routes["routes"][0]["route_id"] == "repo-native"


def test_host_pending_attempt_and_reconcile_cli_round_trip(
    tmp_path,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    dispatch_id = _queue_host_dispatch(state_db)

    code = portal_cli.entrypoint([
        "host", "pending",
        "--state-db", str(state_db),
        "--session-id", "portfolio",
    ])
    assert code == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending["mode"] == "PORTAL_HOST_PENDING_V1"
    assert pending["dispatches"][0]["dispatch_id"] == dispatch_id

    code = portal_cli.entrypoint([
        "host", "attempt",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--attempt-id", "github-call-1",
        "--evidence-id", "github:request:1",
    ])
    assert code == 0
    attempted = json.loads(capsys.readouterr().out)
    assert attempted["dispatch"]["state"] == "ATTEMPTED"

    code = portal_cli.entrypoint([
        "host", "reconcile",
        "--state-db", str(state_db),
        "--dispatch-id", dispatch_id,
        "--state", "VERIFIED_COMPLETE",
        "--evidence-id", "github:commit:deadbeef",
    ])
    assert code == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled["dispatch"]["reconciliation_state"] == "VERIFIED_COMPLETE"


def test_session_run_host_bridge_passes_execution_adapter(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def save_resume_spec(self, **kwargs):
            pass

        def run(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                control_state="RUNNING",
                generation=1,
                wave_run_id="portfolio::g1",
                packets=(),
                summary={"active": 0, "held": 0, "terminal": 0},
            )

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--host-bridge",
        "--once",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_RUN_V1"
    assert isinstance(calls[0]["execution_adapter"], PortalHostExecutionAdapter)


def test_host_bridge_and_process_worker_modes_are_not_implicitly_mixed(
    tmp_path,
    capsys,
) -> None:
    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--host-bridge",
        "--worker-backends", str(tmp_path / "workers.yaml"),
        "--once",
    ])

    assert code == 2
    assert "choose host bridge or worker backends" in capsys.readouterr().err

def test_complete_host_bridge_passes_owning_adapter(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def complete(self, **kwargs):
            calls.append(kwargs)
            return {
                "session_id": kwargs["session_id"],
                "control_state": "RUNNING",
                "generation": 1,
                "summary": {"active": 0, "held": 0, "terminal": 1},
                "subjects": [],
            }

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    code = portal_cli.entrypoint([
        "complete",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--session-id", "portfolio",
        "--subject-id", "portal",
        "--verifier", "vera-review",
        "--host-bridge",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "PORTAL_COMMAND_SESSION_COMPLETE_V1"
    assert isinstance(calls[0]["execution_adapter"], PortalHostExecutionAdapter)

def test_host_occupancy_cli_round_trip(tmp_path, capsys) -> None:
    state_db = tmp_path / "portal.sqlite3"

    code = portal_cli.entrypoint([
        "host", "occupancy",
        "--state-db", str(state_db),
        "--node-id", "lappy",
        "--occupied-slots", "8",
        "--ttl-seconds", "300",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_HOST_OCCUPANCY_ADVERTISE_V1"
    assert payload["occupancy"]["node_id"] == "lappy"
    assert payload["occupancy"]["occupied_slots"] == 8

    code = portal_cli.entrypoint([
        "host", "occupancy-status",
        "--state-db", str(state_db),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "mode": "PORTAL_HOST_OCCUPANCY_STATUS_V1",
        "occupied_node_slots": {"lappy": 8},
    }


def test_session_run_can_require_fresh_host_occupancy_for_unsourced_nodes(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def run_until_idle(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                session_id=kwargs["session_id"],
                control_state="RUNNING",
                cycles=(),
                stop_reason="WAITING_ACTIVE",
                idle_cycles=1,
                summary={"active": 1, "held": 0, "terminal": 0},
            )

        def save_resume_spec(self, **_kwargs):
            return None

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    store.advertise_node_occupancy(
        node_id="lappy",
        occupied_slots=8,
        ttl_seconds=300.0,
    )
    store.close()

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(state_db),
        "--occupied-node", "worklaptop=1",
        "--host-node-occupancy",
    ])

    assert code == 0
    capsys.readouterr()
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["occupied_node_slots"] is None
    provider = kwargs["node_occupancy_provider"]
    assert provider() == {"lappy": 8, "worklaptop": 1}


def test_host_occupancy_missing_unsourced_node_fails_closed(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    class FakeSession:
        def __init__(self, path):
            pass

        def close(self):
            pass

        def run_until_idle(self, **kwargs):
            kwargs["node_occupancy_provider"]()
            raise AssertionError("provider should fail before scheduling")

    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    state_db = tmp_path / "portal.sqlite3"

    code = portal_cli.entrypoint([
        "run",
        "--session-id", "portfolio",
        "--nodes", str(ROOT / "tests" / "fixtures" / "portal-nodes-valid.yaml"),
        "--state-db", str(state_db),
        "--occupied-node", "worklaptop=1",
        "--host-node-occupancy",
    ])

    assert code == 2
    assert "missing current host occupancy for node: lappy" in capsys.readouterr().err



def test_host_pump_cli_loads_manifest_and_runs_existing_pump(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    state_db = tmp_path / "portal.sqlite3"
    drivers_path = tmp_path / "drivers.yaml"
    drivers_path.write_text(
        "schema: PORTAL_HOST_COMMAND_DRIVERS_V1\ndrivers: []\n",
        encoding="utf-8",
    )
    fake_drivers = {"workbridge": object()}
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        portal_cli,
        "load_host_command_drivers",
        lambda path: (
            calls.append({"drivers_path": path}) or fake_drivers
        ),
        raising=False,
    )

    class FakePump:
        def __init__(self, *, store, drivers):
            calls.append({"store": store, "drivers": drivers})

        def run_once(self, *, session_id=None, max_dispatches=None):
            calls.append(
                {
                    "session_id": session_id,
                    "max_dispatches": max_dispatches,
                }
            )
            return SimpleNamespace(
                items=(
                    SimpleNamespace(
                        dispatch_id="dispatch-1",
                        adapter_id="workbridge",
                        route_id="lappy",
                        state="VERIFIED_COMPLETE",
                        evidence_id="receipt-1",
                        reason=None,
                    ),
                ),
                attempted=1,
                verified_complete=1,
                verified_held=0,
                in_progress=0,
                outcome_unknown=0,
                no_driver=0,
                race_lost=0,
                route_unqualified=0,
            )

    monkeypatch.setattr(
        portal_cli,
        "PortalHostPump",
        FakePump,
        raising=False,
    )

    code = portal_cli.entrypoint([
        "host", "pump",
        "--state-db", str(state_db),
        "--drivers", str(drivers_path),
        "--session-id", "portfolio",
        "--max-dispatches", "2",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_HOST_PUMP_V1"
    assert payload["attempted"] == 1
    assert payload["verified_complete"] == 1
    assert payload["items"][0]["dispatch_id"] == "dispatch-1"
    assert calls[0] == {"drivers_path": drivers_path}
    assert calls[1]["drivers"] is fake_drivers
    assert calls[2] == {
        "session_id": "portfolio",
        "max_dispatches": 2,
    }
