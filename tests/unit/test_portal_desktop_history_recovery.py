from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from portal.desktop_app import DesktopSnapshot, DesktopViewModel, PortalDesktopApp
from portal.desktop_ipc import DesktopRuntimeCommandHandler
from portal.desktop_runtime import CognitionLedger, CognitionRequestEnvelope


def _store(ledger, rid: str, source: str, task: str,
           response: str | None, created: float) -> None:
    ledger.store(
        CognitionRequestEnvelope(
            request_id=rid, source=source,
            reason="desktop conversation message", task=task, created_at=created,
        ),
        state="COMPLETED" if response is not None else "UNRESOLVED",
        retryable=False, now=created,
        response_text=response,
    )


def test_desktop_history_reads_actual_human_ledger_ordered_and_bounded(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    ledger = CognitionLedger(db)
    _store(ledger, "second", "HUMAN", "second task", "second reply", 20.0)
    _store(ledger, "autonomous", "PRE_ACTIVE_AUTONOMOUS_TURN", "private autonomous", "internal", 25.0)
    _store(ledger, "first", "HUMAN", "first task", "first reply", 10.0)
    _store(ledger, "third", "HUMAN", "unfinished task", None, 30.0)

    class Engine:
        def __init__(self, ledger): self.ledger = ledger

    handler = DesktopRuntimeCommandHandler(
        engine=Engine(ledger),
        discover_routes=lambda: (),
        runtime_status=lambda: {"state": "ACTIVE"},
    )
    history = handler.handle({"command": "desktop_conversation_history", "limit": 2})
    assert history["schema"] == "PORTAL_DESKTOP_CONVERSATION_HISTORY_V1"
    assert [r["request_id"] for r in history["items"]] == ["second", "third"]
    assert history["items"][0]["task"] == "second task"
    assert history["items"][0]["response_text"] == "second reply"
    assert history["items"][1]["state"] == "UNRESOLVED"
    assert all(r["source"] == "HUMAN" for r in history["items"])
    assert all(r["protected_effect_authority"] is False for r in history["items"])
    with pytest.raises(ValueError):
        handler.handle({"command": "desktop_conversation_history", "limit": 5000})
    ledger.close()
    reopened = CognitionLedger(db)
    assert [r["request_id"] for r in reopened.list_human_conversation(limit=2)] == ["second", "third"]
    reopened.close()


def test_desktop_view_model_recovers_history_and_old_host_is_explicitly_unavailable() -> None:
    class Host:
        def __init__(self, supports): self.supports = supports
        def request(self, command, **kwargs):
            if command == "desktop_conversation_history":
                if not self.supports:
                    raise RuntimeError("unsupported command: 'desktop_conversation_history'")
                return {"schema": "PORTAL_DESKTOP_CONVERSATION_HISTORY_V1", "items": [{"request_id": "saved", "task": "hello",
                                    "state": "COMPLETED", "response_text": "hi"}]}
            raise AssertionError(command)

    assert DesktopViewModel(Host(True)).fetch_conversation_history()[0]["request_id"] == "saved"
    assert DesktopViewModel(Host(False)).fetch_conversation_history() is None


def test_desktop_restore_replays_each_saved_turn_once_and_does_not_invent_reply() -> None:
    app = PortalDesktopApp.__new__(PortalDesktopApp)
    shown = []
    app._displayed_turns = {}
    app._append_conversation = lambda speaker, text: shown.append((speaker, text))
    rows = (
        {"request_id": "a", "task": "hello", "state": "COMPLETED", "response_text": "hi"},
        {"request_id": "b", "task": "working", "state": "UNRESOLVED", "response_text": None},
    )
    app._restore_history(rows)
    app._restore_history(rows)
    assert shown == [("You", "hello"), ("Vera", "hi"), ("You", "working")]
    app._restore_history((dict(rows[1], state="COMPLETED", response_text="done"),))
    assert shown[-1] == ("Vera", "done")
    app._restore_history(rows)
    assert shown.count(("Vera", "hi")) == 1
    assert shown.count(("Vera", "done")) == 1


def test_desktop_history_excludes_installer_human_probes(tmp_path: Path) -> None:
    db = CognitionLedger(tmp_path / "qualification-history.sqlite3")
    try:
        db.store(
            CognitionRequestEnvelope(
                request_id="qualification-human-123", source="HUMAN",
                reason="staged runtime qualification", task="Reply with TOKEN",
                created_at=1.0,
            ),
            state="COMPLETED", retryable=False, now=1.0,
            response_text="TOKEN",
        )
        _store(db, "desktop-user", "HUMAN", "hello", "hi", 2.0)
        assert [r["request_id"] for r in db.list_human_conversation(limit=50)] == [
            "desktop-user",
        ]
    finally:
        db.close()
