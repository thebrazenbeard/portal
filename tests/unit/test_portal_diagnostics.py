from __future__ import annotations

from pathlib import Path

from portal.diagnostics import build_host_diagnostics
from portal.route_resolver import PortalRouteAdvertisement


class FakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def pending_dispatch_diagnostics(self, *, session_id: str):
        assert session_id == "portfolio"
        return (
            {
                "dispatch_id": "stale",
                "subject_id": "alpha",
                "route_qualified": False,
            },
            {
                "dispatch_id": "ready",
                "subject_id": "beta",
                "route_qualified": True,
            },
        )

    def unresolved_dispatches(self, *, session_id: str):
        assert session_id == "portfolio"
        return (
            {
                "dispatch_id": "unknown",
                "subject_id": "gamma",
                "state": "ATTEMPTED",
                "reconciliation_state": "OUTCOME_UNKNOWN",
            },
        )

    def active_routes(self):
        return (
            PortalRouteAdvertisement(
                adapter_id="github",
                route_id="repo-native",
                node_id="repo-native",
                target_kind="repository",
                target_id="thebrazenbeard/beta",
                capabilities=("semantic_work",),
                effect_capabilities=("SOURCE_ONLY",),
                authorized_effects=("SOURCE_ONLY",),
                available=True,
                attached=True,
                current=True,
                preference=50,
            ),
        )

    def active_node_occupancy(self):
        return {"lappy": 8, "worklaptop": 0}


def test_host_diagnostics_are_client_independent_and_attention_oriented() -> None:
    payload = build_host_diagnostics(
        FakeStore(Path("portal.sqlite3")),
        session_id="portfolio",
    )

    assert payload["pending"]["count"] == 2
    assert payload["pending"]["qualified"] == 1
    assert payload["pending"]["unqualified"] == 1
    assert payload["unresolved"]["count"] == 1
    assert payload["routes"]["count"] == 1
    assert payload["node_occupancy"] == {"lappy": 8, "worklaptop": 0}
    assert payload["attention"] == {
        "state": "ACTION_REQUIRED",
        "ready_to_attempt": 1,
        "route_refresh_required": 1,
        "reconciliation_required": 1,
    }
