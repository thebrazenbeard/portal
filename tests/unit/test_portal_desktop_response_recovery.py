"""Response availability never grants a fresh dispatch or source substitution."""
import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from portal.desktop_app import DesktopViewModel
from portal.desktop_host import _atomic_json
from portal.desktop_ipc import BridgeOutcomeUnknownError, FileBridgeClient
from test_desktop_mono_canonical_intake import desktop, handler_for, payload
from test_portal_desktop_canonical_bridge import setup_claim, send


@pytest.fixture
def held_reply(tmp_path, desktop):
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("no route/model calls"))
    result = handler.handle(payload())
    layout, claim = setup_claim(tmp_path / "transport")
    response = layout["responses"] / claim.name
    _atomic_json(response, {"ok": True, "request_id": claim.stem, "result": result})
    return layout, claim, response, result


def clock(monkeypatch):
    # No scheduler timing or Windows AV is needed to reproduce the denial.
    now = [0.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr("portal.desktop_ipc.time", SimpleNamespace(
        monotonic=lambda: now[0], sleep=sleep))
    return now, sleeps


def unreadable(monkeypatch, response, fail):
    read_text = Path.read_text
    attempts = []

    def read(path, *args, **kwargs):
        if path == response:
            attempts.append(path)
            fail(len(attempts))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    return attempts


def denied():
    return PermissionError(errno.EACCES, "injected response read denial")


def assert_retained(layout, claim, claim_bytes, response, response_bytes, desktop):
    assert claim.read_bytes() == claim_bytes
    assert response.read_bytes() == response_bytes
    assert not list(layout["requests"].iterdir())
    assert len(desktop[0].tasks.events()) == 1
    assert desktop[1].list_recent() == []


@pytest.mark.parametrize("denials", [1, 2])
def test_transient_response_denial_retries_only_read_and_replays_same_task(
        held_reply, desktop, monkeypatch, denials):
    layout, claim, response, result = held_reply
    original, reply = claim.read_bytes(), response.read_bytes()
    _, sleeps = clock(monkeypatch)

    def fail(attempt):
        if attempt <= denials:
            raise denied()

    attempts = unreadable(monkeypatch, response, fail)
    # An existing claimed operation must not mint any ID, even a temp filename.
    monkeypatch.setattr("portal.desktop_ipc.uuid.uuid4", lambda: pytest.fail("no replacement ID"))
    client = FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03, poll_seconds=0.01)
    first = send(client)
    replay = send(client)
    assert first["task_id"] == replay["task_id"] == result["task_id"]
    assert first["source_digest"] == result["source_digest"]
    assert len(attempts) == denials + 2 and len(sleeps) == denials
    assert_retained(layout, claim, original, response, reply, desktop)


def test_persistent_response_denial_is_bounded_unknown_and_keeps_ui_pending(
        held_reply, desktop, monkeypatch):
    layout, claim, response, _ = held_reply
    original, reply = claim.read_bytes(), response.read_bytes()
    vm = DesktopViewModel(FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03,
                                          poll_seconds=0.01),
                          conversation_id=payload()["conversation_id"])
    now, sleeps = clock(monkeypatch)

    def fail(_):
        raise denied()

    attempts = unreadable(monkeypatch, response, fail)
    with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
        vm.send_message(payload()["task"], request_id=claim.stem)
    assert vm._pending == (payload()["conversation_id"], claim.stem, payload()["task"])
    assert len(attempts) == len(sleeps) == 3 and now[0] == pytest.approx(0.03)
    with pytest.raises(BridgeOutcomeUnknownError, match="no replacement"):
        vm.send_message("altered literal")
    with pytest.raises(ValueError, match="different original source"):
        send(vm.client, {**payload(), "task": "altered literal"})
    assert len(attempts) == 3
    assert_retained(layout, claim, original, response, reply, desktop)


def test_non_permission_io_failure_is_unknown_without_retry(held_reply, desktop, monkeypatch):
    layout, claim, response, _ = held_reply
    original, reply = claim.read_bytes(), response.read_bytes()
    _, sleeps = clock(monkeypatch)

    def fail(_):
        raise OSError(errno.EIO, "injected I/O error")

    attempts = unreadable(monkeypatch, response, fail)
    with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
        send(FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03, poll_seconds=0.01))
    assert len(attempts) == 1 and sleeps == []
    assert_retained(layout, claim, original, response, reply, desktop)


def test_denied_then_disappearing_response_retains_queued_source(held_reply, desktop, monkeypatch):
    layout, claim, response, _ = held_reply
    # Hostile mixed transport evidence cannot be classified as safely unclaimed.
    queued = layout["requests"] / claim.name
    claim.rename(queued)
    original = queued.read_bytes()
    clock(monkeypatch)

    def disappear(_):
        response.unlink()
        raise denied()

    attempts = unreadable(monkeypatch, response, disappear)
    with pytest.raises(BridgeOutcomeUnknownError, match="no redispatch"):
        send(FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03, poll_seconds=0.01))
    assert len(attempts) == 1 and queued.read_bytes() == original
    assert len(desktop[0].tasks.events()) == 1


@pytest.mark.parametrize("fault", ["partial", "missing", "denied"])
def test_response_publication_race_recovers_only_matching_result(held_reply, desktop, monkeypatch, fault):
    layout, claim, response, result = held_reply
    original, reply = claim.read_bytes(), response.read_bytes()
    _, sleeps = clock(monkeypatch)

    def fail(attempt):
        if attempt != 1:
            return
        if fault == "partial":
            raise json.JSONDecodeError("incomplete", "{", 1)
        if fault == "missing":
            raise FileNotFoundError(errno.ENOENT, "publication race")
        raise denied()

    unreadable(monkeypatch, response, fail)
    value = send(FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03, poll_seconds=0.01))
    assert value["task_id"] == result["task_id"] and len(sleeps) == 1
    assert_retained(layout, claim, original, response, reply, desktop)


@pytest.mark.parametrize("fault", ["partial", "stale", "missing-claim-response"])
def test_unsafe_responses_remain_unknown_without_redispatch(held_reply, desktop, monkeypatch, fault):
    layout, claim, response, result = held_reply
    clock(monkeypatch)
    if fault == "partial":
        response.write_text("{", encoding="utf-8")
    elif fault == "stale":
        _atomic_json(response, {"ok": True, "request_id": claim.stem,
                                "result": {**result, "text": "stale different source"}})
    else:
        response.unlink()
    original = claim.read_bytes()
    with pytest.raises(BridgeOutcomeUnknownError):
        send(FileBridgeClient(layout["runtime_root"], timeout_seconds=0.03, poll_seconds=0.01))
    assert claim.read_bytes() == original and not list(layout["requests"].iterdir())
    assert len(desktop[0].tasks.events()) == 1 and desktop[1].list_recent() == []
