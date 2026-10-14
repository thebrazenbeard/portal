"""Recover an actual Desktop message via the file bridge and durable SQLite.

Only the model provider is deterministic: the file bridge, host handler,
cognition engine/ledger, and UI history rendering are unmocked.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from portal.desktop_app import DesktopViewModel, PortalDesktopApp
from portal.desktop_cognition import CognitionRoute
from portal.desktop_ipc import DesktopRuntimeCommandHandler, FileBridgeClient
from portal.desktop_runtime import CognitionLedger, ResidentCognitionEngine


def test_real_desktop_file_bridge_cognition_persists_and_recovers(tmp_path: Path) -> None:
    db = tmp_path / "state" / "desktop-cognition.sqlite3"
    requests = tmp_path / "bridge" / "requests"
    claimed = tmp_path / "bridge" / "claimed"
    responses = tmp_path / "bridge" / "responses"
    for folder in (requests, claimed, responses):
        folder.mkdir(parents=True)
    server_errors: list[Exception] = []

    def server() -> None:
        # SQLite stays with its owning resident host thread.
        ledger = CognitionLedger(db)
        try:
            local = CognitionRoute(
                route_id="local:proof",
                provider="ollama",
                model_or_agent="proof",
                local=True, available=True, current=True,
                capabilities=("text",),
                incremental_paid_compute=False,
                auto_admissible=True,
                effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
                preference=10,
            )
            engine = ResidentCognitionEngine(
                ledger=ledger,
                discover_routes=lambda: (local,),
                invoke_route=lambda _route, task: "Verified answer: " + task,
            )
            handler = DesktopRuntimeCommandHandler(
                engine=engine,
                discover_routes=lambda: (local,),
                runtime_status=lambda: {"state": "ACTIVE", "runtime_id": "proof"},
            )
            for _ in range(2):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    found = sorted(requests.glob("*.json"))
                    if found:
                        break
                    time.sleep(.005)
                else:
                    raise TimeoutError("missing Desktop bridge command")
                inbound = found[0]
                claimed_file = claimed / inbound.name
                inbound.rename(claimed_file)
                payload = json.loads(claimed_file.read_text(encoding="utf-8"))
                result = handler.handle(payload)
                response = {
                    "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
                    "ok": True,
                    "request_id": payload["request_id"],
                    "runtime_id": "proof",
                    "result": result,
                }
                (responses / inbound.name).write_text(
                    json.dumps(response), encoding="utf-8",
                )
                claimed_file.unlink()
        except Exception as exc:
            server_errors.append(exc)
        finally:
            ledger.close()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    model = DesktopViewModel(
        FileBridgeClient(tmp_path, timeout_seconds=5, poll_seconds=.005),
    )
    try:
        result = model.send_message("Hello across restart", request_id="durable-1")
        assert result["state"] == "COMPLETED"
        assert result["response_text"] == "Verified answer: Hello across restart"
        rows = model.fetch_conversation_history()
        assert rows is not None
        assert [row["request_id"] for row in rows] == ["durable-1"]
        assert rows[0]["task"] == "Hello across restart"

        app = PortalDesktopApp.__new__(PortalDesktopApp)
        app._displayed_turns = {}
        displayed: list[tuple[str, str]] = []
        app._append_conversation = lambda who, text: displayed.append((who, text))
        app._restore_history(rows)
        app._restore_history(rows)
        assert displayed == [
            ("You", "Hello across restart"),
            ("Vera", "Verified answer: Hello across restart"),
        ]
    finally:
        t.join(timeout=5)
    assert not t.is_alive()
    assert not server_errors

    # Simulate a fresh app/host access after the original host connection closed.
    reopened = CognitionLedger(db)
    try:
        saved = reopened.list_human_conversation(limit=10)
        assert saved[0]["request_id"] == "durable-1"
        assert saved[0]["response_text"] == "Verified answer: Hello across restart"
    finally:
        reopened.close()
