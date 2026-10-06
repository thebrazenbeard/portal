from __future__ import annotations

import json
from pathlib import Path

import portal.cli as portal_cli
from portal.route_resolver import PortalRouteAdvertisement


class FakeSession:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def status(self, session_id: str):
        return {
            "session_id": session_id,
            "control_state": "RUNNING",
            "generation": 4,
            "holder": "vera",
            "summary": {"active": 2, "held": 0, "terminal": 3},
            "subjects": [],
        }


class FakeHostStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass

    def pending_dispatch_diagnostics(self, *, session_id: str):
        assert session_id == "portfolio"
        return (
            {
                "dispatch_id": "queued-stale",
                "subject_id": "alpha",
                "adapter_id": "github",
                "route_id": "github:alpha",
                "node_id": "repo-native",
                "state": "QUEUED",
                "route_qualified": False,
            },
            {
                "dispatch_id": "queued-current",
                "subject_id": "beta",
                "adapter_id": "workbridge",
                "route_id": "workbridge:beta",
                "node_id": "worklaptop",
                "state": "QUEUED",
                "route_qualified": True,
            },
        )

    def unresolved_dispatches(self, *, session_id: str):
        assert session_id == "portfolio"
        return (
            {
                "dispatch_id": "attempted-unknown",
                "subject_id": "gamma",
                "adapter_id": "executor",
                "route_id": "WorkLaptop:g9",
                "node_id": "worklaptop",
                "state": "ATTEMPTED",
                "reconciliation_state": "OUTCOME_UNKNOWN",
            },
        )

    def active_routes(self):
        return (
            PortalRouteAdvertisement(
                adapter_id="workbridge",
                route_id="workbridge:beta",
                node_id="worklaptop",
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


def test_session_status_host_details_exposes_stale_and_unresolved_work(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(portal_cli, "PortalCommandSession", FakeSession)
    monkeypatch.setattr(portal_cli, "PortalHostBridgeStore", FakeHostStore)

    code = portal_cli.entrypoint([
        "status",
        "--state-db", str(tmp_path / "portal.sqlite3"),
        "--session-id", "portfolio",
        "--host-details",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTAL_COMMAND_SESSION_STATUS_V1"
    assert payload["host"]["pending"]["count"] == 2
    assert payload["host"]["pending"]["qualified"] == 1
    assert payload["host"]["pending"]["unqualified"] == 1
    assert payload["host"]["pending"]["items"][0]["subject_id"] == "alpha"
    assert payload["host"]["unresolved"]["count"] == 1
    assert payload["host"]["unresolved"]["items"][0]["subject_id"] == "gamma"
    assert payload["host"]["routes"]["count"] == 1
    assert payload["host"]["routes"]["items"][0]["adapter_id"] == "workbridge"
    assert payload["host"]["node_occupancy"] == {
        "lappy": 8,
        "worklaptop": 0,
    }
