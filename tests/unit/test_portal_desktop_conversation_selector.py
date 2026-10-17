"""Client-owned selector metadata is untrusted; never task or memory storage."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from portal.desktop_app import DesktopConversationSelector, DesktopViewModel
from portal.desktop_ipc import BridgeOutcomeUnknownError


class NoClient:
    def request(self, *_args, **_kwargs):
        pytest.fail("blocked UI must not send a bridge request")


def test_missing_pointer_requires_explicit_new_conversation(tmp_path):
    selector = DesktopConversationSelector(tmp_path)
    vm = DesktopViewModel(NoClient(), selector=selector)
    assert selector.read() is None
    with pytest.raises(ValueError, match="CONVERSATION_LOCATOR_REQUIRED"):
        vm.send_message("hello")
    assert not selector.path.exists()
    pytest.importorskip("r8a0.portable_lock")
    first = vm.begin_new_conversation()
    assert first.startswith("desktop-")
    assert len(first) == 40
    assert DesktopViewModel(NoClient(), selector=DesktopConversationSelector(tmp_path)).conversation_id == first
    assert set(json.loads(selector.path.read_text())) == {"schema", "conversation_id"}
    selector.path.unlink()
    with pytest.raises(ValueError, match="SELECTOR_CHANGED"):
        vm.send_message("hello")
    assert not selector.path.exists()


@pytest.mark.parametrize("contents", [
    "{", "[]", '{"schema":"wrong","conversation_id":"desktop-a"}',
    '{"schema":"PORTAL_DESKTOP_UI_SELECTOR_V1","conversation_id":"bad!"}',
    '{"schema":"PORTAL_DESKTOP_UI_SELECTOR_V1","conversation_id":"a","task":"secret"}',
    '{"schema":"PORTAL_DESKTOP_UI_SELECTOR_V1","conversation_id":"a","conversation_id":"b"}',
    " " * 4097,
])
def test_corrupt_pointer_blocks_without_reset_or_replacement(tmp_path, contents):
    selector = DesktopConversationSelector(tmp_path)
    selector.path.parent.mkdir(parents=True)
    selector.path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="SELECTOR_CORRUPT"):
        DesktopViewModel(NoClient(), selector=selector)
    pytest.importorskip("r8a0.portable_lock")
    with pytest.raises(ValueError, match="SELECTOR_CORRUPT"):
        selector.begin_new(expected=None)
    assert selector.path.read_text(encoding="utf-8") == contents


def test_concurrent_processes_cannot_silently_replace_new_conversation(tmp_path):
    pytest.importorskip("r8a0.portable_lock")
    program = '''
import sys
from pathlib import Path
from portal.desktop_app import DesktopConversationSelector
try:
 print(DesktopConversationSelector(Path(sys.argv[1])).begin_new(expected=None))
except ValueError:
 print("BLOCKED")
'''
    def child(_index):
        return subprocess.run([sys.executable, "-B", "-c", program, str(tmp_path)],
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                              text=True, capture_output=True, timeout=20)
    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(child, range(2)))
    assert all(c.returncode == 0 for c in children), [c.stderr for c in children]
    replies = [c.stdout.strip() for c in children]
    assert replies.count("BLOCKED") == 1
    winner = next(r for r in replies if r != "BLOCKED")
    selector = DesktopConversationSelector(tmp_path)
    assert selector.read() == winner
    with pytest.raises(ValueError, match="SELECTOR_CHANGED"):
        selector.begin_new(expected=None)
    assert selector.read() == winner
    next_locator = selector.begin_new(expected=winner)
    assert next_locator != winner


def test_conflicting_selected_conversation_blocks_send(tmp_path):
    pytest.importorskip("r8a0.portable_lock")
    selector = DesktopConversationSelector(tmp_path)
    first = selector.begin_new(expected=None)
    vm = DesktopViewModel(NoClient(), selector=selector)
    with pytest.raises(ValueError, match="SELECTOR_CHANGED"):
        DesktopViewModel(NoClient(), selector=selector, conversation_id="different")
    selector.begin_new(expected=first)
    with pytest.raises(ValueError, match="SELECTOR_CHANGED"):
        vm.send_message("hello")


def test_ambiguous_send_retains_original_message_id_and_bytes():
    calls = []
    class UnknownClient:
        def request(self, command, **body):
            calls.append(body)
            raise BridgeOutcomeUnknownError("claimed; no response")
    vm = DesktopViewModel(UnknownClient(), conversation_id="desktop-test")
    with pytest.raises(BridgeOutcomeUnknownError):
        vm.send_message("same literal text")
    with pytest.raises(BridgeOutcomeUnknownError):
        vm.send_message("same literal text")
    assert calls[0]["request_id"] == calls[1]["request_id"]
    assert calls[0]["task"] == calls[1]["task"] == "same literal text"
    with pytest.raises(BridgeOutcomeUnknownError, match="no replacement"):
        vm.send_message("changed text")
    with pytest.raises(BridgeOutcomeUnknownError, match="no replacement"):
        vm.send_message("same literal text", request_id="replacement")
    assert len(calls) == 2
