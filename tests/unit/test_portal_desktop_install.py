from __future__ import annotations

import json
from pathlib import Path

import pytest

from portal.desktop_install import (
    InstallSpec,
    RuntimeSource,
    StageRootConflict,
    freeze_install_spec,
    prepare_stage_root,
)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40


def test_freeze_install_spec_binds_exact_four_sources() -> None:
    values = {
        ("thebrazenbeard/vera-mono", "main"): SHA_A,
        ("thebrazenbeard/pre-active", "main"): SHA_B,
        ("thebrazenbeard/volition", "main"): SHA_C,
    }

    spec = freeze_install_spec(
        install_id="desktop-v1",
        portal_ref="work/portal-desktop-vera-runtime-v1",
        portal_sha=SHA_D,
        resolve_remote=lambda repository, ref: values[(repository, ref)],
    )

    assert spec.schema == "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1"
    assert [source.component for source in spec.sources] == [
        "vera-mono",
        "portal",
        "pre-active",
        "volition",
    ]
    assert all(len(source.sha) == 40 for source in spec.sources)
    assert spec.source_map()["portal"].sha == SHA_D


def test_install_spec_roundtrips_canonical_json(tmp_path: Path) -> None:
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="one",
        sources=(
            RuntimeSource("vera-mono", "thebrazenbeard/vera-mono", "main", SHA_A),
            RuntimeSource("portal", "thebrazenbeard/portal", "work/x", SHA_B),
            RuntimeSource("pre-active", "thebrazenbeard/pre-active", "main", SHA_C),
            RuntimeSource("volition", "thebrazenbeard/volition", "main", SHA_D),
        ),
    )
    path = tmp_path / "spec.json"
    spec.write(path)

    loaded = InstallSpec.read(path)
    assert loaded == spec
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["sources"][0]["component"] == "vera-mono"


def test_stage_root_is_idempotent_for_same_spec(tmp_path: Path) -> None:
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="one",
        sources=(
            RuntimeSource("vera-mono", "thebrazenbeard/vera-mono", "main", SHA_A),
            RuntimeSource("portal", "thebrazenbeard/portal", "work/x", SHA_B),
            RuntimeSource("pre-active", "thebrazenbeard/pre-active", "main", SHA_C),
            RuntimeSource("volition", "thebrazenbeard/volition", "main", SHA_D),
        ),
    )
    stage = tmp_path / "stage"

    first = prepare_stage_root(stage, spec)
    second = prepare_stage_root(stage, spec)

    assert first == second
    assert (stage / "RUNTIME_INSTALL_SPEC.json").is_file()
    assert (stage / "sources").is_dir()
    assert (stage / "state").is_dir()
    assert (stage / "bridge" / "requests").is_dir()
    assert (stage / "bridge" / "responses").is_dir()
    assert (stage / "logs").is_dir()


def test_stage_root_rejects_different_exact_source_spec(tmp_path: Path) -> None:
    original = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="one",
        sources=(
            RuntimeSource("vera-mono", "thebrazenbeard/vera-mono", "main", SHA_A),
            RuntimeSource("portal", "thebrazenbeard/portal", "work/x", SHA_B),
            RuntimeSource("pre-active", "thebrazenbeard/pre-active", "main", SHA_C),
            RuntimeSource("volition", "thebrazenbeard/volition", "main", SHA_D),
        ),
    )
    changed = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="two",
        sources=(
            RuntimeSource("vera-mono", "thebrazenbeard/vera-mono", "main", SHA_A),
            RuntimeSource("portal", "thebrazenbeard/portal", "work/x", "e" * 40),
            RuntimeSource("pre-active", "thebrazenbeard/pre-active", "main", SHA_C),
            RuntimeSource("volition", "thebrazenbeard/volition", "main", SHA_D),
        ),
    )
    stage = tmp_path / "stage"
    prepare_stage_root(stage, original)

    with pytest.raises(StageRootConflict):
        prepare_stage_root(stage, changed)


def test_invalid_or_duplicate_sources_are_rejected() -> None:
    with pytest.raises(ValueError, match="exactly four"):
        InstallSpec(
            schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
            install_id="bad",
            sources=(),
        ).validate()

    with pytest.raises(ValueError, match="duplicate"):
        InstallSpec(
            schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
            install_id="bad",
            sources=(
                RuntimeSource("vera-mono", "x/y", "main", SHA_A),
                RuntimeSource("vera-mono", "x/z", "main", SHA_B),
                RuntimeSource("pre-active", "x/p", "main", SHA_C),
                RuntimeSource("volition", "x/v", "main", SHA_D),
            ),
        ).validate()
