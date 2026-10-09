from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

import pytest

from portal.desktop_cognition import CognitionRoute
from portal.desktop_ipc import (
    BridgeOutcomeUnknownError,
    BridgeTimeoutError,
    DesktopRuntimeCommandHandler,
    FileBridgeClient,
)
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
                "source": "HUMAN",
                "reason": "test",
                "task": "think",
                "created_at": 100.0,
            }
        )

        activity = handler.handle({"command": "desktop_recent_activity", "limit": 5})
        assert activity["items"][0]["request_id"] == "recent-1"
        assert activity["items"][0]["source"] == "HUMAN"
    finally:
        ledger.close()


def test_handler_rejects_forged_autonomous_source(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, _task: "must not run",
        )
        handler = DesktopRuntimeCommandHandler(
            engine=engine,
            discover_routes=lambda: (LOCAL,),
            runtime_status=lambda: {"state": "ACTIVE"},
        )

        with pytest.raises(ValueError, match="source must be HUMAN"):
            handler.handle(
                {
                    "command": "desktop_cognize",
                    "request_id": "forged-autonomous",
                    "source": "PRE_ACTIVE_AUTONOMOUS_TURN",
                    "reason": "forge provenance",
                    "task": "think",
                    "created_at": 1.0,
                }
            )

        assert ledger.get("forged-autonomous") is None
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
        assert (responses / "fixed.json").exists()
        assert client.request("desktop_status", request_id="fixed") == result
        with pytest.raises(ValueError, match="different payload"):
            client.request("desktop_recent_activity", request_id="fixed")
    finally:
        thread.join(timeout=2)


def test_file_bridge_client_timeout_before_claim_removes_request(tmp_path: Path) -> None:
    client = FileBridgeClient(
        tmp_path,
        timeout_seconds=0.05,
        poll_seconds=0.005,
    )

    with pytest.raises(BridgeTimeoutError, match="before host claim"):
        client.request("desktop_status", request_id="never-claimed")

    assert not (
        tmp_path / "bridge" / "requests" / "never-claimed.json"
    ).exists()
    cancelled = tmp_path / "bridge" / "cancelled" / "never-claimed.json"
    assert json.loads(cancelled.read_text())["command"] == "desktop_status"
    with pytest.raises(BridgeTimeoutError):
        client.request("desktop_status", request_id="never-claimed")
    assert not (tmp_path / "bridge/requests/never-claimed.json").exists()


def test_timeout_losing_atomic_claim_race_is_unknown_not_cancelled(
    tmp_path: Path, monkeypatch,
) -> None:
    client = FileBridgeClient(tmp_path, timeout_seconds=0.01, poll_seconds=0.002)
    replace = os.replace
    claimed = tmp_path / "bridge/claimed/raced.json"
    claimed.parent.mkdir(parents=True)

    def claim_before_cancel(source, destination):
        if Path(destination).parent.name == "cancelled":
            replace(source, claimed)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", claim_before_cancel)
    with pytest.raises(BridgeOutcomeUnknownError, match="claimed"):
        client.request("desktop_status", request_id="raced")
    assert claimed.exists()
    assert not (tmp_path / "bridge/cancelled/raced.json").exists()
    with pytest.raises(BridgeOutcomeUnknownError):
        client.request("desktop_status", request_id="raced")
    assert not (tmp_path / "bridge/requests/raced.json").exists()


def test_response_at_timeout_boundary_is_returned_and_durable(
    tmp_path: Path, monkeypatch,
) -> None:
    client = FileBridgeClient(tmp_path, timeout_seconds=0.01, poll_seconds=0.002)
    replace = os.replace
    claimed = tmp_path / "bridge/claimed/late.json"
    claimed.parent.mkdir(parents=True)
    response = tmp_path / "bridge/responses/late.json"

    def finish_before_cancel(source, destination):
        if Path(destination).parent.name == "cancelled":
            replace(source, claimed)
            response.write_text(json.dumps({"request_id": "late", "ok": True, "result": {"state": "ACTIVE"}}))
        replace(source, destination)

    monkeypatch.setattr(os, "replace", finish_before_cancel)
    assert client.request("desktop_status", request_id="late") == {"state": "ACTIVE"}
    assert response.exists()
    assert claimed.exists()


def test_identical_claimed_retry_ignores_caller_timestamp_without_resubmission(tmp_path: Path) -> None:
    claimed = tmp_path / "bridge/claimed/retry.json"
    claimed.parent.mkdir(parents=True)
    claimed.write_text(json.dumps({
        "command": "desktop_cognize", "request_id": "retry", "task": "hello",
        "source": "HUMAN", "reason": "test", "created_at": 1.0,
    }))
    client = FileBridgeClient(tmp_path, timeout_seconds=0.01, poll_seconds=0.002)
    with pytest.raises(BridgeOutcomeUnknownError):
        client.request("desktop_cognize", request_id="retry", task="hello", source="HUMAN", reason="test", created_at=2.0)
    assert not (tmp_path / "bridge/requests/retry.json").exists()
    with pytest.raises(ValueError, match="different payload"):
        client.request("desktop_cognize", request_id="retry", task="changed", source="HUMAN", reason="test")


def test_file_bridge_client_claimed_request_timeout_is_outcome_unknown(
    tmp_path: Path,
) -> None:
    requests = tmp_path / "bridge" / "requests"
    claimed = tmp_path / "bridge" / "claimed"
    responses = tmp_path / "bridge" / "responses"
    requests.mkdir(parents=True)
    claimed.mkdir(parents=True)
    responses.mkdir(parents=True)

    def server() -> None:
        request_path = requests / "claimed-timeout.json"
        deadline = time.time() + 2
        while not request_path.exists() and time.time() < deadline:
            time.sleep(0.005)
        request_path.replace(claimed / request_path.name)

    thread = threading.Thread(target=server)
    thread.start()
    try:
        client = FileBridgeClient(
            tmp_path,
            timeout_seconds=0.08,
            poll_seconds=0.005,
        )
        with pytest.raises(
            BridgeOutcomeUnknownError,
            match="claimed but no response",
        ):
            client.request(
                "desktop_status",
                request_id="claimed-timeout",
            )
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

def test_transient_response_sharing_denial_keeps_same_request_polling(tmp_path, monkeypatch):
    path = tmp_path / 'response.json'
    path.write_text(json.dumps({'request_id': 'stable', 'ok': True, 'result': {'state': 'ACTIVE'}}))
    original = Path.read_text
    attempts = []
    def flaky(self, *args, **kwargs):
        if self == path:
            attempts.append(self)
            if len(attempts) == 1:
                raise PermissionError('Windows transient sharing denial')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', flaky)
    assert FileBridgeClient._response(path, 'stable') is None
    assert FileBridgeClient._response(path, 'stable') == {'state': 'ACTIVE'}


def test_retry_recovers_submission_interrupted_before_request_publication(tmp_path: Path, monkeypatch) -> None:
    client = FileBridgeClient(tmp_path, timeout_seconds=2.0, poll_seconds=0.002)
    replace = os.replace
    request_path = tmp_path / "bridge/requests/interrupted.json"
    submission_path = tmp_path / "bridge/submissions/interrupted.json"
    response_path = tmp_path / "bridge/responses/interrupted.json"

    def interrupted_publication(source, destination):
        if Path(destination) == request_path:
            raise OSError("client interrupted after durable submission")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupted_publication)
    with pytest.raises(OSError, match="client interrupted"):
        client.request("desktop_status", request_id="interrupted", created_at=10.0)
    assert submission_path.exists()
    assert not request_path.exists()
    monkeypatch.setattr(os, "replace", replace)
    observed = []

    def server():
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                data = json.loads(request_path.read_text())
                request_path.replace(tmp_path / "bridge/claimed/interrupted.json")
            except (FileNotFoundError, PermissionError):
                # Atomic rename and transient Windows sharing may race with a read.
                time.sleep(0.002)
                continue
            observed.append(data)
            staged_response = response_path.with_suffix(".json.tmp")
            staged_response.write_text(json.dumps({
                "request_id": "interrupted", "ok": True, "result": {"state": "ACTIVE"},
            }))
            staged_response.replace(response_path)
            return

    thread = threading.Thread(target=server)
    thread.start()
    try:
        assert client.request("desktop_status", request_id="interrupted", created_at=20.0) == {"state": "ACTIVE"}
    finally:
        thread.join(timeout=1)
    assert len(observed) == 1
    assert observed[0]["created_at"] == 10.0
    assert json.loads(submission_path.read_text())["created_at"] == 10.0


@pytest.mark.parametrize("retained_state", ["requests", "claimed", "cancelled"])
def test_retry_validates_all_retained_identities_before_recovery(tmp_path: Path, retained_state: str) -> None:
    client = FileBridgeClient(tmp_path, timeout_seconds=0.01, poll_seconds=0.002)
    marker = tmp_path / "bridge/submissions/conflict.json"
    other = tmp_path / "bridge" / retained_state / "conflict.json"
    marker.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"command": "desktop_status", "request_id": "conflict"}))
    other.write_text(json.dumps({"command": "desktop_recent_activity", "request_id": "conflict"}))
    with pytest.raises(ValueError, match="different payload"):
        client.request("desktop_status", request_id="conflict")
    assert json.loads(other.read_text())["command"] == "desktop_recent_activity"
