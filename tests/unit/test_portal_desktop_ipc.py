from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from portal.desktop_cognition import CognitionRoute
from portal.desktop_ipc import DesktopRuntimeCommandHandler, FileBridgeClient
from portal.desktop_runtime import CognitionLedger, ResidentCognitionEngine


LOCAL = CognitionRoute(
    route_id="ollama:vera-local:latest",
    provider="ollama",
    model_or_agent="vera-local:latest",
    local=True,
    available=True,
    current=True,
    capabilities=("text",),
    incremental_paid_compute=False,
    auto_admissible=True,
    effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
    preference=10,
)


def test_handler_discovers_routes_and_cognizes_through_resident_engine(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, task: f"answer:{task}",
        )
        handler = DesktopRuntimeCommandHandler(
            engine=engine,
            discover_routes=lambda: (LOCAL,),
            runtime_status=lambda: {"state": "ACTIVE", "runtime_id": "r1"},
        )

        routes = handler.handle({"command": "desktop_discover_routes"})
        assert routes["routes"][0]["route_id"] == "ollama:vera-local:latest"

        result = handler.handle(
            {
                "command": "desktop_cognize",
                "request_id": "ipc-1",
                "source": "HUMAN",
                "reason": "desktop message",
                "task": "hello",
                "created_at": 100.0,
            }
        )
        assert result["state"] == "COMPLETED"
        assert result["response_text"] == "answer:hello"
        assert result["protected_effect_authority"] is False

        status = handler.handle({"command": "desktop_status"})
        assert status == {"state": "ACTIVE", "runtime_id": "r1"}
    finally:
        ledger.close()


def test_handler_recent_activity_returns_persisted_cognition(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, _task: "ok",
        )
        handler = DesktopRuntimeCommandHandler(
            engine=engine,
            discover_routes=lambda: (LOCAL,),
            runtime_status=lambda: {"state": "ACTIVE"},
        )
        handler.handle(
            {
                "command": "desktop_cognize",
                "request_id": "recent-1",
                "source": "PRE_ACTIVE_AUTONOMOUS_TURN",
                "reason": "test",
                "task": "think",
                "created_at": 100.0,
            }
        )

        activity = handler.handle({"command": "desktop_recent_activity", "limit": 5})
        assert activity["items"][0]["request_id"] == "recent-1"
        assert activity["items"][0]["source"] == "PRE_ACTIVE_AUTONOMOUS_TURN"
    finally:
        ledger.close()


def test_file_bridge_client_uses_existing_request_response_envelope(tmp_path: Path) -> None:
    requests = tmp_path / "bridge" / "requests"
    responses = tmp_path / "bridge" / "responses"
    requests.mkdir(parents=True)
    responses.mkdir(parents=True)

    def server() -> None:
        request_path = requests / "fixed.json"
        deadline = time.time() + 2
        while not request_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        assert payload["request_id"] == "fixed"
        assert payload["command"] == "desktop_status"
        (responses / "fixed.json").write_text(
            json.dumps(
                {
                    "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
                    "request_id": "fixed",
                    "runtime_id": "runtime-1",
                    "ok": True,
                    "result": {"state": "ACTIVE"},
                    "observed_at": 100.0,
                }
            ),
            encoding="utf-8",
        )

    thread = threading.Thread(target=server)
    thread.start()
    try:
        client = FileBridgeClient(tmp_path, timeout_seconds=2.0)
        result = client.request("desktop_status", request_id="fixed")
        assert result == {"state": "ACTIVE"}
        assert not (responses / "fixed.json").exists()
    finally:
        thread.join(timeout=2)


def test_file_bridge_client_surfaces_host_error(tmp_path: Path) -> None:
    requests = tmp_path / "bridge" / "requests"
    responses = tmp_path / "bridge" / "responses"
    requests.mkdir(parents=True)
    responses.mkdir(parents=True)

    def server() -> None:
        request_path = requests / "bad.json"
        deadline = time.time() + 2
        while not request_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        (responses / "bad.json").write_text(
            json.dumps(
                {
                    "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
                    "request_id": "bad",
                    "runtime_id": "runtime-1",
                    "ok": False,
                    "error": "ValueError: nope",
                    "observed_at": 100.0,
                }
            ),
            encoding="utf-8",
        )

    thread = threading.Thread(target=server)
    thread.start()
    try:
        client = FileBridgeClient(tmp_path, timeout_seconds=2.0)
        with pytest.raises(RuntimeError, match="ValueError: nope"):
            client.request("desktop_status", request_id="bad")
    finally:
        thread.join(timeout=2)
