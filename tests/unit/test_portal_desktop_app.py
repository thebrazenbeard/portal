from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Queue
import threading

import pytest

from portal.desktop_app import DesktopViewModel, PortalDesktopApp
from portal.desktop_supervisor import RuntimeState, RuntimeStatus


@dataclass
class FakeClient:
    calls: list[tuple[str, dict[str, object]]]

    def request(self, command: str, **payload: object) -> dict[str, object]:
        self.calls.append((command, payload))
        if command == "desktop_status":
            return {
                "state": "ACTIVE",
                "runtime_id": "r1",
                "selected_route_id": "ollama:vera-local:latest",
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
        if command == "desktop_authority":
            return {
                "decision": str(payload["action"]).upper(),
                "request_id": payload["request_id"],
                "execution_performed": False,
            }
        raise AssertionError(command)


def test_view_model_authority_actions_use_resident_ipc_without_executing_effect() -> None:
    client = FakeClient([])
    vm = DesktopViewModel(client)

    approved = vm.approve_authority("authority-1", valid_for_seconds=120.0)
    denied = vm.deny_authority("authority-2")

    assert approved["decision"] == "APPROVE"
    assert approved["execution_performed"] is False
    assert denied["decision"] == "DENY"
    assert client.calls[-2:] == [
        (
            "desktop_authority",
            {
                "action": "approve",
                "request_id": "authority-1",
                "valid_for_seconds": 120.0,
            },
        ),
        (
            "desktop_authority",
            {
                "action": "deny",
                "request_id": "authority-2",
            },
        ),
    ]


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
    assert not any(command == "desktop_discover_routes" for command, _ in client.calls)
    assert snapshot.components == {"vera_mono": {"loaded": True}}


@pytest.mark.parametrize("state", [RuntimeState.OFFLINE, RuntimeState.STARTING, RuntimeState.DEGRADED, RuntimeState.BLOCKED])
def test_refresh_reports_supervisor_state_without_claiming_runtime_is_active(state) -> None:
    class Supervisor:
        def ensure_started(self):
            return RuntimeStatus(state, "observed failure", heartbeat_age_seconds=8.0)

    client = FakeClient([])
    snapshot = DesktopViewModel(client, supervisor=Supervisor()).refresh()
    assert snapshot.runtime_state == state.value
    assert snapshot.health_reason == "observed failure"
    assert snapshot.heartbeat_age_seconds == 8.0
    assert client.calls == []


def test_missing_installation_is_blocked_and_actionable() -> None:
    class Supervisor:
        def ensure_started(self):
            raise FileNotFoundError("runtime python not found: example/python.exe")

    snapshot = DesktopViewModel(FakeClient([]), supervisor=Supervisor()).refresh()
    assert snapshot.runtime_state == "BLOCKED"
    assert "runtime python not found" in snapshot.health_reason


def test_no_resident_route_is_not_inferred_from_local_model_discovery() -> None:
    class Client(FakeClient):
        def request(self, command, **payload):
            result = super().request(command, **payload)
            result.pop("selected_route_id", None)
            return result

    assert DesktopViewModel(Client([])).refresh().current_route is None


def test_refresh_is_bounded_and_workers_never_call_tk() -> None:
    entered, released = threading.Event(), threading.Event()

    class ViewModel:
        def refresh(self):
            entered.set()
            assert released.wait(2)
            raise RuntimeError("delayed failure")

    class Root:
        def after(self, *args):
            assert threading.current_thread() is threading.main_thread()

    class Variable:
        value = ""
        def set(self, value):
            assert threading.current_thread() is threading.main_thread()
            self.value = value

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app.root = Root()
    app.health_var = Variable()
    app.view_model = ViewModel()
    app._events = Queue()
    app._closed = False
    app._refresh_in_flight = False
    app.refresh_async()
    assert entered.wait(2)
    app.refresh_async()
    released.set()
    event = app._events.get(timeout=2)
    app._events.put(event)
    app._drain_events()
    assert "delayed failure" in app.health_var.value
    assert not app._refresh_in_flight
    assert app._events.empty()


def test_close_only_destroys_window_and_ignores_pending_callbacks() -> None:
    class Root:
        destroyed = False
        def destroy(self):
            self.destroyed = True

    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app.root = Root()
    app._closed = False
    app._events = Queue()
    app._events.put(("refresh_error", "failure"))
    app.close()
    app._drain_events()
    assert app.root.destroyed


def test_main_accepts_explicit_runtime_root(monkeypatch, tmp_path: Path) -> None:
    import portal.desktop_app as module
    observed = []
    class App:
        def __init__(self, *, runtime_root):
            observed.append(runtime_root)
        def run(self):
            pass
    monkeypatch.setattr(module, "PortalDesktopApp", App)
    module.main(["--runtime-root", str(tmp_path)])
    assert observed == [tmp_path]


def test_default_runtime_root_is_user_local(monkeypatch, tmp_path: Path) -> None:
    import portal.desktop_app as module
    monkeypatch.delenv("VERA_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert module.default_runtime_root() == tmp_path / "PortalVera" / "runtime"


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


def test_portfolio_controls_go_through_resident_ipc():
    calls = []
    class Client:
        def request(self, command, **payload):
            calls.append((command, payload))
            return {"session": {"control_state": "RUNNING"}}
    assert DesktopViewModel(Client()).portfolio("run", session_id="work")["session"]["control_state"] == "RUNNING"
    assert calls == [("desktop_portfolio", {"action": "run", "session_id": "work"})]


def test_default_selects_existing_explicitly_activated_install(monkeypatch, tmp_path):
    import json
    import portal.desktop_app as module
    monkeypatch.delenv("VERA_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installed = tmp_path / "VeraDesktopRuntime" / "qualified"
    installed.mkdir(parents=True)
    (installed / "RUNTIME_INSTALL_SPEC.json").write_text(json.dumps({"activation": {"active": True, "qualified": True}}))
    (installed / "QUALIFICATION.json").write_text(json.dumps({"qualified": True, "qualified_at": 10}))
    assert module.default_runtime_root() == installed

def test_loaded_runtime_ipc_cannot_override_stale_supervisor_health():
    class Supervisor:
        def ensure_started(self):
            return RuntimeStatus(RuntimeState.DEGRADED, 'heartbeat_stale_process_alive', components_loaded=True, heartbeat_age_seconds=8)
    snapshot = DesktopViewModel(FakeClient([]), supervisor=Supervisor()).refresh()
    assert snapshot.runtime_state == 'DEGRADED'
    assert snapshot.health_reason == 'heartbeat_stale_process_alive'


@pytest.mark.parametrize(
    ("subject", "label"),
    [
        ({"state": "ACTIVE", "verification_state": None, "dispatch_state": "FAILED_RETRYABLE"}, "FAILED_RETRYABLE"),
        ({"state": "ACTIVE", "verification_state": None, "dispatch_state": "FAILED_DETERMINISTIC"}, "FAILED_DETERMINISTIC"),
        ({"state": "ACTIVE", "verification_state": None, "dispatch_state": "OUTCOME_UNKNOWN"}, "OUTCOME_UNKNOWN"),
        ({"state": "ACTIVE", "verification_state": None, "dispatch_state": "DISPATCHED"}, "Pending"),
        ({"state": "HELD", "verification_state": "VERIFIED_HELD", "dispatch_state": "FAILED_RETRYABLE"}, "VERIFIED_HELD"),
    ],
)
def test_desktop_portfolio_displays_worker_failures_instead_of_pending(subject, label):
    assert PortalDesktopApp._portfolio_result_label(subject) == label
