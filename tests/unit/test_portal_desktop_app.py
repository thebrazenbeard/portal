from __future__ import annotations

from dataclasses import dataclass

from portal.desktop_app import DesktopViewModel


@dataclass
class FakeClient:
    calls: list[tuple[str, dict[str, object]]]

    def request(self, command: str, **payload: object) -> dict[str, object]:
        self.calls.append((command, payload))
        if command == "desktop_status":
            return {
                "state": "ACTIVE",
                "runtime_id": "r1",
                "components": {"vera_mono": {"loaded": True}},
            }
        if command == "desktop_discover_routes":
            return {
                "routes": [
                    {
                        "route_id": "codex:cli",
                        "local": False,
                        "available": True,
                        "current": True,
                        "incremental_paid_compute": None,
                        "auto_admissible": False,
                        "preference": 50,
                    },
                    {
                        "route_id": "ollama:vera-local:latest",
                        "local": True,
                        "available": True,
                        "current": True,
                        "incremental_paid_compute": False,
                        "auto_admissible": True,
                        "preference": 10,
                    },
                ]
            }
        if command == "desktop_recent_activity":
            return {
                "items": [
                    {
                        "request_id": "auto-1",
                        "source": "PRE_ACTIVE_AUTONOMOUS_TURN",
                        "reason": "open loop",
                        "state": "COMPLETED",
                        "route_id": "ollama:vera-local:latest",
                        "updated_at": 100.0,
                        "protected_effect_authority": False,
                    }
                ]
            }
        if command == "desktop_cognize":
            return {
                "request_id": payload["request_id"],
                "state": "COMPLETED",
                "route_id": "ollama:vera-local:latest",
                "response_text": "hello Patrick",
                "retryable": False,
                "evidence_id": "cognition:x",
                "protected_effect_authority": False,
            }
        raise AssertionError(command)


def test_view_model_refresh_reports_runtime_route_and_last_autonomous_activity() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    snapshot = vm.refresh()

    assert snapshot.runtime_state == "ACTIVE"
    assert snapshot.runtime_id == "r1"
    assert snapshot.current_route == "ollama:vera-local:latest"
    assert snapshot.last_autonomous_activity is not None
    assert snapshot.last_autonomous_activity["request_id"] == "auto-1"
    assert snapshot.pending_effects == ()


def test_view_model_send_message_uses_ipc_runtime_not_direct_model() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    response = vm.send_message(
        "hello",
        request_id="human-fixed",
        created_at=123.0,
    )

    assert response["response_text"] == "hello Patrick"
    command, payload = client.calls[-1]
    assert command == "desktop_cognize"
    assert payload["request_id"] == "human-fixed"
    assert payload["source"] == "HUMAN"
    assert payload["reason"] == "desktop conversation message"
    assert payload["task"] == "hello"


def test_view_model_rejects_empty_message_without_ipc_call() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    try:
        vm.send_message("   ")
    except ValueError as exc:
        assert "message" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert client.calls == []
