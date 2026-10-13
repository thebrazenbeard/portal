from __future__ import annotations

from portal.desktop_host import _DESKTOP_IPC_COMMANDS


def test_installed_host_forwards_bounded_conversation_history() -> None:
    assert "desktop_conversation_history" in _DESKTOP_IPC_COMMANDS
