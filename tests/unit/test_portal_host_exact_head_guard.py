from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import portal.cli as portal_cli
from portal.adapters import PortalRouteBinding
from portal.host_bridge import PortalHostBridgeStore
from portal.host_command_driver import PortalCommandHostDriver
from portal.host_pump import PortalHostDriverResult, PortalHostPump
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _queued_store(path: Path, *, head_reader=None):
    store = PortalHostBridgeStore(path, head_reader=head_reader)
    packet = PortalWavePacket(
        run_id="portfolio::g1", subject_id="portal",
        repository="thebrazenbeard/portal", ref="main", exact_head=HEAD_A,
        node_id="repo-native", lane_id="vera", state="CLAIMED",
        plan_sha256="b" * 64, fencing_token=1, lineage_id="lineage:portal",
        work_fingerprint="c" * 64, action="ADVANCE",
        effect_ceiling="SOURCE_ONLY", review_gate="EXACT_HEAD_REVIEW",
        frontier="Advance portal.", lead_identity="vera", reviewer_identities=(),
    )
    result = PortalSessionResult(
        session_id="portfolio", control_state="RUNNING", generation=1,
        wave_run_id=packet.run_id, packets=(packet,),
        summary={"active": 1, "held": 0, "terminal": 0},
    )
    route = PortalRouteBinding(
        subject_kind="repository", subject_id="portal",
        adapter_id="github", route_id="github:portal",
    )
    store.advertise_route(
        PortalRouteAdvertisement(
            adapter_id="github", route_id="github:portal", node_id="repo-native",
            target_kind="repository", target_id="thebrazenbeard/portal",
            capabilities=("semantic_work",),
            effect_capabilities=("SOURCE_ONLY",),
            authorized_effects=("SOURCE_ONLY",), available=True,
            attached=True, current=True, preference=50,
        ), observed_at=0.0, ttl_seconds=1_000_000_000_000.0,
    )
    dispatch = store.queue_dispatch(
        result=result, packet=packet, binding=route, queued_at=100.0,
    )
    return store, str(dispatch["dispatch_id"])


@pytest.mark.parametrize("reader", [None, lambda _repo, _ref: HEAD_B,
                                     lambda _repo, _ref: "not-a-sha"])
def test_mark_attempted_fails_closed_without_matching_head(tmp_path, reader):
    store, dispatch_id = _queued_store(tmp_path / "state.db", head_reader=reader)
    with pytest.raises(ValueError, match="source head"):
        store.mark_attempted(
            dispatch_id=dispatch_id, attempt_id="attempt", evidence_id="receipt",
            attempted_at=110.0,
        )
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_head_reader_fails_outside_sqlite_write_transaction(tmp_path):
    observations = []
    store, dispatch_id = _queued_store(tmp_path / "state.db")

    def reader(repository, ref):
        observations.append(store.connection.in_transaction)
        assert (repository, ref) == ("thebrazenbeard/portal", "main")
        return HEAD_A

    store.head_reader = reader
    attempted = store.mark_attempted(
        dispatch_id=dispatch_id, attempt_id="attempt", evidence_id="receipt",
        attempted_at=110.0,
    )
    assert observations == [False]
    assert attempted["state"] == "ATTEMPTED"
    store.close()


def test_atomic_take_does_not_attempt_stale_head(tmp_path):
    store, dispatch_id = _queued_store(
        tmp_path / "state.db", head_reader=lambda _repo, _ref: HEAD_B,
    )
    with pytest.raises(ValueError, match="source head"):
        store.take_pending_dispatch(
            adapter_ids=("github",), attempt_id="take", evidence_id="receipt",
            attempted_at=110.0,
        )
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_take_reads_head_outside_transaction_and_rejects_changed_binding(tmp_path):
    store, dispatch_id = _queued_store(tmp_path / "state.db")
    transactions = []

    def reader(_repository, _ref):
        transactions.append(store.connection.in_transaction)
        store.connection.execute(
            "UPDATE portal_host_dispatches SET exact_head = ? WHERE dispatch_id = ?",
            (HEAD_B, dispatch_id),
        )
        return HEAD_A

    store.head_reader = reader
    assert store.take_pending_dispatch(
        adapter_ids=("github",), attempt_id="attempt", evidence_id="receipt",
        attempted_at=110.0,
    ) is None
    assert transactions == [False]
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_attempt_rejects_changed_binding_after_head_read(tmp_path):
    store, dispatch_id = _queued_store(tmp_path / "state.db")

    def reader(_repository, _ref):
        assert not store.connection.in_transaction
        store.connection.execute(
            "UPDATE portal_host_dispatches SET exact_head = ? WHERE dispatch_id = ?",
            (HEAD_B, dispatch_id),
        )
        return HEAD_A

    store.head_reader = reader
    with pytest.raises(ValueError, match="binding changed"):
        store.mark_attempted(
            dispatch_id=dispatch_id, attempt_id="attempt", evidence_id="receipt",
            attempted_at=110.0,
        )
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


@pytest.mark.parametrize("operation", ["attempt", "take"])
def test_route_expiring_during_remote_head_read_cannot_be_attempted(
    tmp_path, monkeypatch, operation,
):
    store, dispatch_id = _queued_store(tmp_path / "state.db")
    store.connection.execute(
        "UPDATE portal_host_routes SET observed_at = 100, expires_at = 150"
    )
    clock = [120.0]
    monkeypatch.setattr("portal.host_bridge.time.time", lambda: clock[0])

    def reader(_repository, _ref):
        assert not store.connection.in_transaction
        clock[0] = 151.0  # Remote read took longer than route validity.
        return HEAD_A

    store.head_reader = reader
    if operation == "attempt":
        with pytest.raises(ValueError, match="route is not currently qualified"):
            store.mark_attempted(
                dispatch_id=dispatch_id, attempt_id="attempt", evidence_id="receipt",
            )
    else:
        assert store.take_pending_dispatch(
            adapter_ids=("github",), attempt_id="attempt", evidence_id="receipt",
        ) is None
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_pump_does_not_invoke_driver_with_stale_head(tmp_path):
    store, dispatch_id = _queued_store(
        tmp_path / "state.db", head_reader=lambda _repo, _ref: HEAD_B,
    )
    calls = []

    class Driver:
        def execute(self, dispatch, *, attempt_id):
            calls.append(dispatch)
            return PortalHostDriverResult("VERIFIED_COMPLETE", "receipt")

    result = PortalHostPump(
        store=store, drivers={"github": Driver()},
        attempt_id_factory=lambda _: "attempt",
    ).run_once()
    assert result.attempted == 0
    assert result.source_head_held == 1
    assert result.items[0].state == "SOURCE_HEAD_HELD"
    assert calls == []
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_reader_failure_fails_closed_and_matching_head_is_claimable(tmp_path):
    def unavailable(_repository, _ref):
        raise PermissionError("token unavailable")

    store, dispatch_id = _queued_store(
        tmp_path / "state.db", head_reader=unavailable,
    )
    with pytest.raises(ValueError, match="source head"):
        store.take_pending_dispatch(
            adapter_ids=("github",), attempt_id="attempt", evidence_id="receipt",
            attempted_at=110.0,
        )
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.head_reader = lambda _repository, _ref: HEAD_A
    taken = store.take_pending_dispatch(
        adapter_ids=("github",), attempt_id="attempt", evidence_id="receipt",
        attempted_at=110.0,
    )
    assert taken["state"] == "ATTEMPTED"
    store.head_reader = unavailable
    assert store.mark_attempted(
        dispatch_id=dispatch_id, attempt_id="attempt", evidence_id="receipt",
    ) == store.load_dispatch(dispatch_id)
    store.close()


def test_cli_attempt_and_take_hold_stale_head_without_attempt(tmp_path, monkeypatch, capsys):
    state_db = tmp_path / "state.db"
    store, dispatch_id = _queued_store(state_db)
    store.close()
    monkeypatch.setattr(
        portal_cli, "GitHubRestTransport",
        lambda **_kwargs: SimpleNamespace(read_ref=lambda _r, _f: HEAD_B),
    )

    code = portal_cli.entrypoint([
        "host", "attempt", "--state-db", str(state_db),
        "--dispatch-id", dispatch_id, "--attempt-id", "attempt",
        "--evidence-id", "receipt",
    ])
    assert code == 2
    assert "source head" in capsys.readouterr().err
    code = portal_cli.entrypoint([
        "host", "take", "--state-db", str(state_db),
        "--adapter-id", "github", "--attempt-id", "attempt",
        "--evidence-id", "receipt",
    ])
    assert code == 2
    assert "source head" in capsys.readouterr().err
    store = PortalHostBridgeStore(state_db)
    assert store.load_dispatch(dispatch_id)["state"] == "QUEUED"
    store.close()


def test_command_driver_rechecks_source_before_launch(tmp_path, monkeypatch):
    store, dispatch_id = _queued_store(tmp_path / "state.db")
    dispatch = store.load_dispatch(dispatch_id)
    called = []
    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: called.append(args),
    )
    driver = PortalCommandHostDriver(
        command=("driver",), head_reader=lambda _r, _f: HEAD_B,
    )
    with pytest.raises(ValueError, match="source head is not current"):
        driver.execute(dispatch, attempt_id="attempt")
    assert called == []
    store.close()


def test_pump_verifies_held_if_builtin_driver_detects_late_head_move(
    tmp_path, monkeypatch,
):
    store, dispatch_id = _queued_store(
        tmp_path / "state.db", head_reader=lambda _r, _f: HEAD_A,
    )
    called = []
    monkeypatch.setattr(
        "portal.host_command_driver.subprocess.run",
        lambda *args, **kwargs: called.append(args),
    )
    driver = PortalCommandHostDriver(
        command=("driver",), head_reader=lambda _r, _f: HEAD_B,
    )
    result = PortalHostPump(
        store=store, drivers={"github": driver},
        attempt_id_factory=lambda _: "attempt",
    ).run_once()
    assert called == []
    assert result.attempted == 1  # durable boundary crossed before second read
    assert result.verified_held == 1
    assert result.outcome_unknown == 0
    final = store.load_dispatch(dispatch_id)
    assert final["state"] == "ATTEMPTED"
    assert final["reconciliation_state"] == "VERIFIED_HELD"
    store.close()
