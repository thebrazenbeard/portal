"""Existing file bridge preserves exact claims; uncertainty never republishes."""
import json
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from portal.desktop_host import _atomic_json, claim_bridge_request, prepare_runtime_layout
from portal.desktop_ipc import BridgeOutcomeUnknownError, FileBridgeClient
from test_desktop_mono_canonical_intake import desktop, handler_for, payload


def setup_claim(root, request=None):
    layout = prepare_runtime_layout(root)
    original = payload() if request is None else request
    claim = layout["claimed"] / "fixture-message-1.json"
    claim.write_text(json.dumps(original), encoding="utf-8")
    return layout, claim


def send(client, request=None):
    body = dict(payload() if request is None else request)
    command = body.pop("command")
    return client.request(command, **body)


def test_claimed_missing_response_never_republishes_or_changes_source(tmp_path):
    layout, claim = setup_claim(tmp_path)
    original = claim.read_bytes()
    client = FileBridgeClient(tmp_path, timeout_seconds=0.02, poll_seconds=0.001)
    with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
        send(client)
    assert not list(layout["requests"].glob("*.json"))
    assert claim.read_bytes() == original
    for altered in ({**payload(), "task": "substituted"},
                    {**payload(), "conversation_id": "other-conversation"}):
        with pytest.raises(ValueError, match="different original source"):
            send(client, altered)
    assert claim.read_bytes() == original


def test_partial_claimed_response_is_unknown_and_evidence_is_retained(tmp_path):
    layout, claim = setup_claim(tmp_path)
    response = layout["responses"] / claim.name
    response.write_text("{", encoding="utf-8")
    client = FileBridgeClient(tmp_path, timeout_seconds=0.02, poll_seconds=0.001)
    with pytest.raises(BridgeOutcomeUnknownError, match="incomplete bridge response"):
        send(client)
    assert response.read_text() == "{"
    assert claim.exists()
    assert not list(layout["requests"].glob("*.json"))


@pytest.mark.parametrize("result", [
    {"state": "COMPLETED", "response_text": "fabricated"},
    None,
    {"schema": "PORTAL_DESKTOP_CANONICAL_INTAKE_V1", "state": "HELD", "origin_authenticated": True},
])
def test_fabricated_success_response_never_becomes_canonical_result(tmp_path, result):
    layout, claim = setup_claim(tmp_path)
    response = layout["responses"] / claim.name
    _atomic_json(response, {"ok": True, "request_id": "fixture-message-1", "result": result})
    with pytest.raises(BridgeOutcomeUnknownError, match="unverified"):
        send(FileBridgeClient(tmp_path, timeout_seconds=0.02, poll_seconds=0.001))
    assert claim.exists() and response.exists()
    assert not list(layout["requests"].glob("*.json"))


def test_actual_host_claim_and_handler_roundtrip_and_transport_replay(tmp_path, desktop):
    runtime, ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    layout = prepare_runtime_layout(tmp_path / "bridge-root")
    client = FileBridgeClient(layout["runtime_root"], timeout_seconds=3, poll_seconds=0.001)

    def serve_once():
        request_path = layout["requests"] / "fixture-message-1.json"
        deadline = time.monotonic() + 2
        while not request_path.exists() and time.monotonic() < deadline:
            time.sleep(0.001)
        claim = claim_bridge_request(request_path, layout["claimed"])
        request = json.loads(claim.read_text(encoding="utf-8"))
        # The canonical handler does not enter the thread-affine operational DB.
        result = handler.handle(request)
        _atomic_json(layout["responses"] / claim.name,
                     {"ok": True, "request_id": request["request_id"], "result": result})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(serve_once)
        first = send(client)
        future.result(timeout=3)
    replay = send(client, {**payload(), "created_at": 999.0})
    assert replay["task_id"] == first["task_id"]
    assert first["transport_replayed"] is False
    assert replay["transport_replayed"] is True
    assert replay["task_head_current"] is False
    assert len(runtime.tasks.events()) == 1
    assert ledger.list_recent() == []


@pytest.mark.parametrize("request_id", [".legacy", "-legacy", "x" * 97, ""])
def test_canonical_transport_rejects_ids_outside_mono_subset_before_publication(tmp_path, request_id):
    client = FileBridgeClient(tmp_path)
    with pytest.raises(ValueError):
        send(client, {**payload(), "request_id": request_id})
    assert not (tmp_path / "bridge").exists()


def test_cold_ui_restores_unknown_original_id_from_existing_claim(tmp_path):
    from portal.desktop_app import DesktopConversationSelector, DesktopViewModel

    selector = DesktopConversationSelector(tmp_path)
    conversation_id = selector.begin_new(expected=None)
    original = {**payload(), "conversation_id": conversation_id}
    layout, _claim = setup_claim(tmp_path, original)
    vm = DesktopViewModel(FileBridgeClient(tmp_path), selector=DesktopConversationSelector(tmp_path))
    with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
        vm.send_message(original["task"])
    assert vm._pending[1] == "fixture-message-1"
    with pytest.raises(BridgeOutcomeUnknownError, match="no replacement"):
        vm.send_message("changed after restart")
    assert not list(layout["requests"].glob("*.json"))
