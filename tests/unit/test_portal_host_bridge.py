from __future__ import annotations

from pathlib import Path

import pytest

import portal.session as portal_session
from portal.host_bridge import PortalHostBridgeStore, PortalHostExecutionAdapter
from portal.models import ExecutionNode
from portal.route_resolver import PortalRouteAdvertisement
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket, PortalWavePreparationResult
from runner.portfolio_wave_scheduler import WaveExecutionBudget


def _packet(run_id: str = "portfolio::g1") -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
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


def _result(run_id: str = "portfolio::g1") -> PortalSessionResult:
    return PortalSessionResult(
        session_id="portfolio",
        control_state="RUNNING",
        generation=1,
        wave_run_id=run_id,
        packets=(_packet(run_id),),
        summary={"active": 1, "held": 0, "terminal": 0},
    )


def _route(**overrides) -> PortalRouteAdvertisement:
    values = dict(
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
    values.update(overrides)
    return PortalRouteAdvertisement(**values)


def test_host_route_advertisement_expires_and_fails_closed(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)

    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    bindings = adapter.select_routes(_result())
    assert [(item.adapter_id, item.route_id) for item in bindings] == [
        ("github", "repo-native")
    ]

    expired = PortalHostExecutionAdapter(store=store, clock=lambda: 161.0)
    with pytest.raises(ValueError, match="no qualified execution route"):
        expired.select_routes(_result())

    store.close()


def test_host_dispatch_is_durable_and_idempotent_before_external_attempt(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    result = _result()
    bindings = adapter.select_routes(result)

    first = adapter.dispatch(result, bindings)
    second = adapter.dispatch(result, bindings)

    assert first == second
    assert first[0].state == "HOST_QUEUED"
    assert first[0].evidence_id.startswith("host-dispatch:")

    pending = store.pending_dispatches(session_id="portfolio")
    assert len(pending) == 1
    item = pending[0]
    assert item["state"] == "QUEUED"
    assert item["wave_run_id"] == "portfolio::g1"
    assert item["subject_id"] == "portal"
    assert item["adapter_id"] == "github"
    assert item["route_id"] == "repo-native"
    assert item["repository"] == "thebrazenbeard/portal"
    assert item["exact_head"] == "a" * 40
    store.close()


def test_attempt_marker_turns_unfinished_host_effect_into_outcome_unknown(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    result = _result()
    bindings = adapter.select_routes(result)
    dispatch = adapter.dispatch(result, bindings)[0]
    dispatch_id = dispatch.evidence_id.removeprefix("host-dispatch:")

    attempted = store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
        attempted_at=121.0,
    )
    assert attempted["state"] == "ATTEMPTED"

    records = adapter.reconcile(
        {
            "subjects": [
                {
                    "subject_kind": "repository",
                    "subject_id": "portal",
                    "state": "ACTIVE",
                    "wave_run_id": "portfolio::g1",
                    "adapter_id": "github",
                    "route_id": "repo-native",
                }
            ]
        },
        (
            {
                "subject_kind": "repository",
                "subject_id": "portal",
                "state": "ACTIVE",
                "wave_run_id": "portfolio::g1",
                "adapter_id": "github",
                "route_id": "repo-native",
            },
        ),
    )
    assert len(records) == 1
    assert records[0].state == "OUTCOME_UNKNOWN"
    assert records[0].evidence_id == "github:request:1"
    store.close()


def test_host_reconciliation_can_verify_completion(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    result = _result()
    bindings = adapter.select_routes(result)
    dispatch = adapter.dispatch(result, bindings)[0]
    dispatch_id = dispatch.evidence_id.removeprefix("host-dispatch:")

    store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
        attempted_at=121.0,
    )
    store.record_reconciliation(
        dispatch_id=dispatch_id,
        state="VERIFIED_COMPLETE",
        evidence_id="github:commit:deadbeef",
        reconciled_at=122.0,
    )

    records = adapter.reconcile(
        {"subjects": []},
        (
            {
                "subject_kind": "repository",
                "subject_id": "portal",
                "state": "ACTIVE",
                "wave_run_id": "portfolio::g1",
                "adapter_id": "github",
                "route_id": "repo-native",
            },
        ),
    )
    assert len(records) == 1
    assert records[0].state == "VERIFIED_COMPLETE"
    assert records[0].evidence_id == "github:commit:deadbeef"
    store.close()


def test_different_second_attempt_is_refused_after_effect_boundary(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    result = _result()
    bindings = adapter.select_routes(result)
    dispatch = adapter.dispatch(result, bindings)[0]
    dispatch_id = dispatch.evidence_id.removeprefix("host-dispatch:")

    first = store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
        attempted_at=121.0,
    )
    replay = store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
        attempted_at=121.0,
    )
    assert replay == first

    with pytest.raises(ValueError, match="different attempt"):
        store.mark_attempted(
            dispatch_id=dispatch_id,
            attempt_id="github-call-2",
            evidence_id="github:request:2",
            attempted_at=123.0,
        )
    store.close()


def test_verified_complete_requires_prior_attempt(tmp_path: Path) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    result = _result()
    bindings = adapter.select_routes(result)
    dispatch = adapter.dispatch(result, bindings)[0]
    dispatch_id = dispatch.evidence_id.removeprefix("host-dispatch:")

    with pytest.raises(ValueError, match="must be attempted"):
        store.record_reconciliation(
            dispatch_id=dispatch_id,
            state="VERIFIED_COMPLETE",
            evidence_id="github:commit:deadbeef",
            reconciled_at=122.0,
        )
    store.close()

def test_complete_uses_owning_host_adapter_reconciliation(
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

    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    store.advertise_route(
        _route(),
        ttl_seconds=60.0,
        observed_at=100.0,
    )
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    controller = portal_session.PortalCommandSession(state_db)
    controller.run(
        session_id="portfolio",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="repo-native", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        execution_adapter=adapter,
    )

    pending = store.pending_dispatches(session_id="portfolio")
    dispatch_id = pending[0]["dispatch_id"]
    store.mark_attempted(
        dispatch_id=dispatch_id,
        attempt_id="github-call-1",
        evidence_id="github:request:1",
        attempted_at=121.0,
    )
    store.record_reconciliation(
        dispatch_id=dispatch_id,
        state="VERIFIED_COMPLETE",
        evidence_id="github:commit:deadbeef",
        reconciled_at=122.0,
    )

    status = controller.complete(
        session_id="portfolio",
        holder="vera",
        subject_kind="repository",
        subject_id="portal",
        verifier="vera-review",
        token=None,
        execution_adapter=adapter,
    )

    assert status["summary"] == {"active": 0, "held": 0, "terminal": 1}
    assert status["subjects"][0]["verification_state"] == "VERIFIED_COMPLETE"
    controller.close()
    store.close()


def test_complete_refuses_route_bound_subject_without_owning_adapter(
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
    state_db = tmp_path / "portal.sqlite3"
    store = PortalHostBridgeStore(state_db)
    store.advertise_route(_route(), ttl_seconds=60.0, observed_at=100.0)
    adapter = PortalHostExecutionAdapter(store=store, clock=lambda: 120.0)
    controller = portal_session.PortalCommandSession(state_db)
    controller.run(
        session_id="portfolio",
        holder="vera",
        wave_path=tmp_path / "wave.json",
        corpus_path=tmp_path / "corpus.json",
        projects_path=tmp_path / "projects.yaml",
        nodes=(ExecutionNode(node_id="repo-native", max_parallel=1),),
        budget=WaveExecutionBudget(1, 1, 1, 1),
        lease_ttl=300.0,
        token=None,
        execution_adapter=adapter,
    )

    with pytest.raises(ValueError, match="owning execution adapter"):
        controller.complete(
            session_id="portfolio",
            holder="vera",
            subject_kind="repository",
            subject_id="portal",
            verifier="vera-review",
            token=None,
        )
    controller.close()
    store.close()

def test_bulk_route_advertisement_rolls_back_on_stale_member(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    existing = _route(
        route_id="repo-native:thebrazenbeard/portal",
        target_id="thebrazenbeard/portal",
    )
    store.advertise_route(
        existing,
        ttl_seconds=60.0,
        observed_at=100.0,
    )

    new_route = _route(
        route_id="repo-native:thebrazenbeard/new",
        target_id="thebrazenbeard/new",
    )
    with pytest.raises(ValueError, match="stale host route observation"):
        store.advertise_routes(
            (new_route, existing),
            ttl_seconds=60.0,
            observed_at=99.0,
        )

    active = store.active_routes(now=100.0)
    assert [route.target_id for route in active] == [
        "thebrazenbeard/portal",
    ]
    store.close()



def test_host_snapshot_rolls_back_all_members_on_stale_route(
    tmp_path: Path,
) -> None:
    store = PortalHostBridgeStore(tmp_path / "portal.sqlite3")
    store.advertise_route(
        _route(),
        ttl_seconds=300.0,
        observed_at=200.0,
    )

    with pytest.raises(ValueError, match="stale host route observation"):
        store.advertise_snapshot(
            (
                _route(
                    adapter_id="workbridge",
                    route_id="lappy:portal",
                    node_id="lappy",
                    authorized_effects=(),
                ),
                _route(),
            ),
            {"lappy": 3},
            ttl_seconds=300.0,
            observed_at=150.0,
        )

    routes = store.active_routes(now=220.0)
    assert [(item.adapter_id, item.route_id) for item in routes] == [
        ("github", "repo-native")
    ]
    assert store.active_node_occupancy(now=220.0) == {}
    store.close()
