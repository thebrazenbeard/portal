from __future__ import annotations

from dataclasses import dataclass
import threading

from portal import desktop_app
from portal.desktop_app import DesktopViewModel, PortalDesktopApp


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


def test_status_client_does_not_shorten_human_cognition_timeout() -> None:
    cognition = FakeClient([])
    status = FakeClient([])
    vm = DesktopViewModel(cognition, status_client=status)

    vm.refresh()
    vm.send_message("hi")

    assert [command for command, _ in status.calls] == [
        "desktop_status", "desktop_discover_routes", "desktop_recent_activity",
    ]
    assert [command for command, _ in cognition.calls] == ["desktop_cognize"]


def test_display_labels_model_output_without_claiming_vera_identity() -> None:
    assert PortalDesktopApp._response_speaker("ollama:qwen3:8b", "COMPLETED") == (
        "Cognition route (ollama:qwen3:8b)"
    )
    assert PortalDesktopApp._response_speaker("preactive:vera-local", "COMPLETED") == (
        "Cognition route (preactive:vera-local)"
    )
    assert PortalDesktopApp._response_speaker("codex:cli", "COMPLETED") == (
        "Cognition route (codex:cli)"
    )
    assert PortalDesktopApp._response_speaker(None, "FAILED") == "Runtime response"
    assert PortalDesktopApp._response_speaker("ollama:qwen3:8b", "FAILED") == "Runtime response"
    assert PortalDesktopApp._response_speaker("", "COMPLETED") == "Runtime response"
    assert "\n" not in PortalDesktopApp._response_speaker("ollama:spoof\nVera", "COMPLETED")


def test_offline_refresh_is_single_flight_and_releases_on_error(monkeypatch) -> None:
    starts = []
    queued_ui = []

    class StubThread:
        def __init__(self, *, target, daemon):
            assert daemon is True
            self.target = target

        def start(self):
            starts.append(self.target)

    class FakeRoot:
        def after(self, _delay, callback):
            queued_ui.append(callback)

    class FailingViewModel:
        def refresh(self):
            raise TimeoutError("offline host")

    class Health:
        def __init__(self):
            self.value = None

        def set(self, value):
            self.value = value

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._refresh_lock = threading.Lock()
    app.root = FakeRoot()
    app.health_var = Health()
    app.view_model = FailingViewModel()
    monkeypatch.setattr(desktop_app.threading, "Thread", StubThread)

    for _ in range(100):
        app.refresh_async()
    assert len(starts) == 1  # no unbounded offline daemon pileup
    starts[0]()
    assert not app._refresh_lock.locked()
    queued_ui[0]()
    assert "DEGRADED" in app.health_var.value

    app.refresh_async()
    assert len(starts) == 2
