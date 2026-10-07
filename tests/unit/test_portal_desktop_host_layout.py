from __future__ import annotations

from pathlib import Path

from portal.desktop_host import (
    claim_bridge_request,
    prepare_runtime_layout,
)


def test_prepare_runtime_layout_creates_component_state_and_bridge_directories(tmp_path: Path) -> None:
    layout = prepare_runtime_layout(tmp_path)

    assert layout["state_root"] == tmp_path / "state"
    for component in ("vera", "portal", "pre-active", "cognition"):
        assert (tmp_path / "state" / component).is_dir()
    assert (tmp_path / "bridge" / "requests").is_dir()
    assert (tmp_path / "bridge" / "claimed").is_dir()
    assert (tmp_path / "bridge" / "responses").is_dir()


def test_claim_bridge_request_moves_request_before_processing(tmp_path: Path) -> None:
    layout = prepare_runtime_layout(tmp_path)
    request_path = layout["requests"] / "r1.json"
    request_path.write_text('{"request_id":"r1"}', encoding="utf-8")

    claimed_path = claim_bridge_request(
        request_path,
        layout["claimed"],
    )

    assert claimed_path == layout["claimed"] / "r1.json"
    assert claimed_path.is_file()
    assert not request_path.exists()


def test_resident_host_routes_explicit_authority_decisions_without_executing_effect():
    from portal.desktop_host import ResidentHost

    calls = []

    class Authority:
        def approve(self, request_id, **kwargs):
            calls.append(("approve", request_id, kwargs))
            return {
                "decision": "APPROVED",
                "execution_performed": False,
            }

        def deny(self, request_id, **kwargs):
            calls.append(("deny", request_id, kwargs))
            return {
                "decision": "DENIED",
                "execution_performed": False,
            }

    host = ResidentHost.__new__(ResidentHost)
    host.authority = Authority()

    approved = host.handle(
        {
            "command": "desktop_authority",
            "action": "approve",
            "request_id": "authority-1",
            "valid_for_seconds": 120,
        }
    )
    denied = host.handle(
        {
            "command": "desktop_authority",
            "action": "deny",
            "request_id": "authority-2",
        }
    )

    assert approved["decision"] == "APPROVED"
    assert approved["execution_performed"] is False
    assert denied["decision"] == "DENIED"
    assert calls[0][0:2] == ("approve", "authority-1")
    assert calls[0][2]["valid_for_seconds"] == 120.0
    assert calls[1][0:2] == ("deny", "authority-2")
