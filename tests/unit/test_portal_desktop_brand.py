from __future__ import annotations

from pathlib import Path

from portal.desktop_app import PORTAL_PALETTE, PortalDesktopApp


def test_vera_brand_palette_preserves_dark_contrast_and_rose_accent() -> None:
    colors = PORTAL_PALETTE
    for key in ("background", "surface", "surface_raised", "editor", "border",
                "text", "muted", "subtle", "accent", "warning"):
        assert len(colors[key]) == 7 and colors[key].startswith("#")
    assert int(colors["background"][1:3], 16) < 48
    assert int(colors["accent"][1:3], 16) > 160


def test_branded_app_still_attributes_model_instead_of_claiming_identity() -> None:
    assert PortalDesktopApp._response_speaker("ollama:gemma3:1b", "COMPLETED") == (
        "Cognition route (ollama:gemma3:1b)"
    )
    assert PortalDesktopApp._response_speaker(None, "FAILED") == "Runtime response"


def test_provided_vera_emblem_is_a_small_packaged_png() -> None:
    from portal import desktop_app
    image = Path(desktop_app.__file__).parent / "assets" / "vera_icon.png"
    assert image.is_file()
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert image.stat().st_size < 50000
