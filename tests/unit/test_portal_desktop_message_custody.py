from __future__ import annotations

import queue
import threading

import pytest

from portal.desktop_app import DesktopViewModel, PortalDesktopApp
from portal.desktop_ipc import BridgeOutcomeUnknownError


def test_view_model_rejects_mismatched_cognition_result_request_id() -> None:
    class Client:
        def request(self, command, *, request_id=None, **payload):
            assert command == "desktop_cognize"
            return {
                "request_id": "other-request",
                "state": "COMPLETED",
                "response_text": "untrusted reply",
            }

    with pytest.raises(ValueError, match="request_id mismatch"):
        DesktopViewModel(Client()).send_message(
            "hello", request_id="bound-request",
        )


def test_gui_uncertain_host_claim_never_invents_vera_reply() -> None:
    app = PortalDesktopApp.__new__(PortalDesktopApp)
    app._displayed_turns = {}
    rendered = []
    callbacks = queue.Queue()
    events = queue.Queue()

    class Input:
        def get(self): return "please think"
        def set(self, text): pass

    class Status:
        def set(self, value): events.put(("status", value))

    class Button:
        def configure(self, **kwargs): events.put(("button", kwargs))

    class ViewModel:
        def send_message(self, message, *, request_id):
            assert message == "please think"
            assert len(request_id) == 32
            raise BridgeOutcomeUnknownError("claimed, response lost")

    class Root:
        def after(self, delay, callback):
            assert delay == 0
            callbacks.put(callback)

    app.message_var = Input()
    app.status_var = Status()
    app.send_button = Button()
    app.view_model = ViewModel()
    app.root = Root()
    app._append_conversation = lambda speaker, text: rendered.append((speaker, text))
    app.refresh_async = lambda: None
    app.send_message()
    callback = callbacks.get(timeout=2.0)
    callback()
    assert rendered[0] == ("You", "please think")
    assert len(rendered) == 2
    assert rendered[1][0] == "System"
    assert "claimed" in rendered[1][1]
    assert not any(speaker == "Vera" for speaker, _ in rendered)
    assert len(app._displayed_turns) == 1
