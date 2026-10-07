from __future__ import annotations

from portal.adapters import (
    PortalDispatchRecord,
    PortalReconciliationRecord,
    PortalRouteBinding,
)
from portal.execution_router import CapabilityExecutionAdapter
from portal.route_resolver import PortalRouteAdvertisement, PortalRouteRequest
from portal.session import PortalSessionResult
from portal.wave_runtime import PortalWavePacket


def _packet(
    run_id: str,
    *,
    subject_id: str,
    repository: str,
    node_id: str,
    lineage_id: str,
) -> PortalWavePacket:
    return PortalWavePacket(
        run_id=run_id,
        subject_id=subject_id,
        repository=repository,
        ref="main",
        exact_head="a" * 40,
        node_id=node_id,
        lane_id="vera",
        state="CLAIMED",
        plan_sha256="b" * 64,
        fencing_token=1,
        lineage_id=lineage_id,
        work_fingerprint="c" * 64,
        action="ADVANCE",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier="advance",
        lead_identity="vera",
        reviewer_identities=(),
    )


def _result() -> PortalSessionResult:
    run_id = "portal::g1"
    return PortalSessionResult(
        session_id="portal",
        control_state="RUNNING",
        generation=1,
        wave_run_id=run_id,
        packets=(
            _packet(
                run_id,
                subject_id="portal",
                repository="thebrazenbeard/portal",
                node_id="repo-native",
                lineage_id="lineage-portal",
            ),
            _packet(
                run_id,
                subject_id="lou-pole",
                repository="thebrazenbeard/lou-pole",
                node_id="worklaptop",
                lineage_id="lineage-lou",
            ),
        ),
        summary={"active": 2, "held": 0, "terminal": 0},
    )


def _request(packet: PortalWavePacket) -> PortalRouteRequest:
    capability = (
        "repository_read"
        if packet.node_id == "repo-native"
        else "process_execute"
    )
    return PortalRouteRequest(
        subject_kind="repository",
        subject_id=packet.subject_id,
        node_id=packet.node_id,
        target_kind="repository",
        target_id=packet.repository,
        required_capabilities=(capability,),
        required_effect="NO_PROTECTED_EFFECT",
    )


def _advertisements(result: PortalSessionResult):
    return (
        PortalRouteAdvertisement(
            adapter_id="github",
            route_id="repo-native",
            node_id="repo-native",
            target_kind="repository",
            target_id="thebrazenbeard/portal",
            capabilities=("repository_read",),
            effect_capabilities=("NO_PROTECTED_EFFECT",),
            authorized_effects=("NO_PROTECTED_EFFECT",),
            available=True,
            attached=True,
            current=True,
            preference=20,
        ),
        PortalRouteAdvertisement(
            adapter_id="workbridge",
            route_id="WorkLaptop",
            node_id="worklaptop",
            target_kind="repository",
            target_id="thebrazenbeard/lou-pole",
            capabilities=("process_execute",),
            effect_capabilities=("NO_PROTECTED_EFFECT",),
            authorized_effects=("NO_PROTECTED_EFFECT",),
            available=True,
            attached=True,
            current=True,
            preference=10,
        ),
    )


class FakeDriver:
    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        self.dispatch_calls: list[tuple[str, ...]] = []
        self.reconcile_calls: list[tuple[str, ...]] = []

    def dispatch(self, result, routes):
        self.dispatch_calls.append(tuple(route.subject_id for route in routes))
        return tuple(
            PortalDispatchRecord(
                subject_kind=route.subject_kind,
                subject_id=route.subject_id,
                adapter_id=route.adapter_id,
                route_id=route.route_id,
                state="DISPATCHED",
                evidence_id=f"{self.adapter_id}:{route.subject_id}:dispatch",
            )
            for route in routes
        )

    def reconcile(self, status, subjects):
        self.reconcile_calls.append(
            tuple(subject["subject_id"] for subject in subjects)
        )
        return tuple(
            PortalReconciliationRecord(
                subject_kind=subject["subject_kind"],
                subject_id=subject["subject_id"],
                adapter_id=subject["adapter_id"],
                route_id=subject["route_id"],
                state="OUTCOME_UNKNOWN",
                evidence_id=f"{self.adapter_id}:{subject['subject_id']}:unknown",
            )
            for subject in subjects
        )


def test_composite_selects_routes_without_hardcoding_adapter_names() -> None:
    adapter = CapabilityExecutionAdapter(
        advertisement_provider=_advertisements,
        request_builder=_request,
        drivers={},
    )

    routes = tuple(adapter.select_routes(_result()))

    assert routes == (
        PortalRouteBinding(
            subject_kind="repository",
            subject_id="portal",
            adapter_id="github",
            route_id="repo-native",
        ),
        PortalRouteBinding(
            subject_kind="repository",
            subject_id="lou-pole",
            adapter_id="workbridge",
            route_id="WorkLaptop",
        ),
    )


def test_dispatch_prevalidates_all_drivers_before_any_effect_attempt() -> None:
    github = FakeDriver("github")
    adapter = CapabilityExecutionAdapter(
        advertisement_provider=_advertisements,
        request_builder=_request,
        drivers={"github": github},
    )
    result = _result()
    routes = tuple(adapter.select_routes(result))

    try:
        adapter.dispatch(result, routes)
    except ValueError as exc:
        assert "missing execution driver" in str(exc)
    else:
        raise AssertionError("missing driver should fail closed")

    assert github.dispatch_calls == []


def test_dispatch_delegates_each_bound_route_only_to_owning_driver() -> None:
    github = FakeDriver("github")
    workbridge = FakeDriver("workbridge")
    adapter = CapabilityExecutionAdapter(
        advertisement_provider=_advertisements,
        request_builder=_request,
        drivers={"github": github, "workbridge": workbridge},
    )
    result = _result()
    routes = tuple(adapter.select_routes(result))

    records = tuple(adapter.dispatch(result, routes))

    assert github.dispatch_calls == [("portal",)]
    assert workbridge.dispatch_calls == [("lou-pole",)]
    assert {record.subject_id for record in records} == {"portal", "lou-pole"}


def test_reconcile_delegates_only_active_route_bound_subjects() -> None:
    github = FakeDriver("github")
    workbridge = FakeDriver("workbridge")
    adapter = CapabilityExecutionAdapter(
        advertisement_provider=_advertisements,
        request_builder=_request,
        drivers={"github": github, "workbridge": workbridge},
    )
    status = {
        "subjects": [
            {
                "subject_kind": "repository",
                "subject_id": "portal",
                "state": "ACTIVE",
                "adapter_id": "github",
                "route_id": "repo-native",
            },
            {
                "subject_kind": "repository",
                "subject_id": "lou-pole",
                "state": "ACTIVE",
                "adapter_id": "workbridge",
                "route_id": "WorkLaptop",
            },
            {
                "subject_kind": "repository",
                "subject_id": "legacy-wave",
                "state": "ACTIVE",
                "adapter_id": None,
                "route_id": None,
            },
            {
                "subject_kind": "repository",
                "subject_id": "finished",
                "state": "TERMINAL",
                "adapter_id": "github",
                "route_id": "repo-native",
            },
        ]
    }

    records = tuple(adapter.reconcile(status))

    assert github.reconcile_calls == [("portal",)]
    assert workbridge.reconcile_calls == [("lou-pole",)]
    assert {record.subject_id for record in records} == {"portal", "lou-pole"}


def test_request_builder_cannot_change_packet_identity_or_placement() -> None:
    def bad_request(packet: PortalWavePacket) -> PortalRouteRequest:
        request = _request(packet)
        return PortalRouteRequest(
            subject_kind=request.subject_kind,
            subject_id=request.subject_id,
            node_id="somewhere-else",
            target_kind=request.target_kind,
            target_id=request.target_id,
            required_capabilities=request.required_capabilities,
            required_effect=request.required_effect,
        )

    adapter = CapabilityExecutionAdapter(
        advertisement_provider=_advertisements,
        request_builder=bad_request,
        drivers={},
    )

    try:
        tuple(adapter.select_routes(_result()))
    except ValueError as exc:
        assert "request does not match admitted packet" in str(exc)
    else:
        raise AssertionError("mismatched request should fail closed")
