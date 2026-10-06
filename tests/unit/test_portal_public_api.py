from __future__ import annotations

import portal


def test_public_api_exposes_execution_routing_contract() -> None:
    expected = {
        "CapabilityExecutionAdapter",
        "LocalProjectRunnerTaskCurrentness",
        "PortalDispatchRecord",
        "PortalHostBridgeStore",
        "PortalHostExecutionAdapter",
        "PortalHostNodeCurrentness",
        "PortalHostDriverResult",
        "PortalHostPump",
        "PortalHostPumpItemResult",
        "PortalHostPumpResult",
        "PortalProposalProcessAdapter",
        "PortalReconciliationRecord",
        "PortalRouteAdvertisement",
        "PortalRouteBinding",
        "PortalRouteRequest",
        "build_host_diagnostics",
        "build_process_proposal_execution_adapter",
        "resolve_portal_route",
    }

    assert expected.issubset(set(portal.__all__))
    for name in expected:
        assert getattr(portal, name) is not None
