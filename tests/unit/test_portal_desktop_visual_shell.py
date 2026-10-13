from __future__ import annotations

import os

import pytest

from portal.desktop_app import PortalDesktopApp, PORTAL_PALETTE


def test_visual_shell_has_explicit_usable_theme() -> None:
    assert PORTAL_PALETTE["background"] != PORTAL_PALETTE["surface"]
    assert PORTAL_PALETTE["text"] != PORTAL_PALETTE["background"]
    assert PORTAL_PALETTE["accent"] != PORTAL_PALETTE["surface"]
    assert PORTAL_PALETTE["warning"] != PORTAL_PALETTE["text"]


@pytest.mark.parametrize("width,height", [(1280, 820), (1100, 760)])
def test_windows_visual_shell_keeps_composer_footer_and_effects_visible(
    tmp_path, monkeypatch, width: int, height: int,
) -> None:
    if os.name != "nt":
        pytest.skip("native Tk layout acceptance runs on Windows")
    monkeypatch.setattr(PortalDesktopApp, "refresh_async", lambda self: None)
    try:
        app = PortalDesktopApp(runtime_root=tmp_path)
    except Exception as exc:
        pytest.skip(f"GUI not available: {exc}")
    try:
        app.root.geometry(f"{width}x{height}+50+50")
        app._show_workspace("vera")
        app.root.update()
        assert app.root.winfo_width() == width
        assert app.root.winfo_height() == height
        for element in (
            app.send_button, app.message_entry, app.effects_label,
            app.footer_status, app.conversation,
        ):
            assert element.winfo_ismapped()
            assert element.winfo_width() >= 10
            assert element.winfo_height() >= 8
            x = element.winfo_rootx() - app.root.winfo_rootx()
            y = element.winfo_rooty() - app.root.winfo_rooty()
            assert x >= -1 and y >= -1, (element, x, y)
            assert x + element.winfo_width() <= width + 1, (element, x)
            assert y + element.winfo_height() <= height + 1, (element, y)
    finally:
        app.root.destroy()



def test_portfolio_surface_is_primary_and_uses_real_curated_registry(tmp_path, monkeypatch) -> None:
    import os
    from pathlib import Path
    if os.name != "nt":
        pytest.skip("Windows GUI required")
    monkeypatch.setattr(PortalDesktopApp, "refresh_async", lambda self: None)
    try:
        app = PortalDesktopApp(runtime_root=tmp_path)
    except Exception as exc:
        pytest.skip(f"GUI not available: {exc}")
    try:
        app.root.update()
        assert app._project_items
        assert len(app._project_items) == 15
        assert "static registry" in app.portfolio_count_var.get()
        assert "not a live GitHub inventory" in app.portfolio_count_var.get()
        assert app._portfolio_panel.winfo_ismapped()
        assert app.project_listbox.size() == len(app._project_items)
        app._show_workspace("vera")
        app.root.update()
        assert app.conversation.winfo_ismapped()
    finally:
        app.root.destroy()
