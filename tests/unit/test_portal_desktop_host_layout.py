from __future__ import annotations

from pathlib import Path

from portal.desktop_host import prepare_runtime_layout


def test_prepare_runtime_layout_creates_component_state_and_bridge_directories(tmp_path: Path) -> None:
    layout = prepare_runtime_layout(tmp_path)

    assert layout["state_root"] == tmp_path / "state"
    for component in ("vera", "portal", "pre-active", "cognition"):
        assert (tmp_path / "state" / component).is_dir()
    assert (tmp_path / "bridge" / "requests").is_dir()
    assert (tmp_path / "bridge" / "responses").is_dir()
