from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_portal_surfaces_preserve_canonical_product_name() -> None:
    """New integration sources must not resurrect retired product spelling."""
    active_files = [
        ROOT / "README.md",
        ROOT / "PORTAL.md",
        *sorted((ROOT / "docs" / "architecture").glob("*.md")),
        *sorted((ROOT / "docs" / "operations").glob("*.md")),
        *sorted((ROOT / "portal").glob("*.py")),
    ]
    stale = [
        str(path.relative_to(ROOT))
        for path in active_files
        if "P.O.R.T.A.L." in path.read_text(encoding="utf-8")
    ]
    assert not stale, f"obsolete product name in active PORTAL sources: {stale}"
