from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from portal.desktop_install import InstallSpec, RuntimeSource
from portal.runtime_source_attestation import (
    FILENAME, build_attestation, verify_attestation, write_attestation,
)


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def _stage(tmp_path: Path) -> InstallSpec:
    sources = []
    for component in ("vera-mono", "portal", "pre-active", "volition"):
        root = tmp_path / "sources" / component
        root.mkdir(parents=True)
        (root / "runtime.py").write_text("ENABLED = True\n", encoding="utf-8")
        _run("git", "-C", str(root), "init", "--quiet")
        _run("git", "-C", str(root), "add", ".")
        _run("git", "-C", str(root), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture")
        sha = _run("git", "-C", str(root), "rev-parse", "HEAD")
        sources.append(RuntimeSource(component, f"thebrazenbeard/{component}", "main", sha))
    return InstallSpec("VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1", "fixture-id", tuple(sources))


def _must_fail(action) -> None:
    try:
        action()
    except RuntimeError:
        return
    raise AssertionError("source mutation must fail attestation")


def test_source_attestation_binds_pin_file_bytes_and_manifest(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    attested = write_attestation(tmp_path, spec)
    expected = hashlib.sha256(b"ENABLED = True\n").hexdigest()
    assert attested["sources"]["portal"]["files"]["runtime.py"]["sha256"] == expected
    assert verify_attestation(tmp_path, spec) == attested
    assert json.loads((tmp_path / FILENAME).read_text())["sha256"] == attested["sha256"]


def test_attestation_rejects_modified_or_deleted_tracked_source(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    write_attestation(tmp_path, spec)
    source = tmp_path / "sources" / "portal" / "runtime.py"
    source.write_text("ENABLED = False\n", encoding="utf-8")
    _must_fail(lambda: verify_attestation(tmp_path, spec))
    source.unlink()
    _must_fail(lambda: verify_attestation(tmp_path, spec))


def test_assume_unchanged_cannot_hide_tampered_pinned_source(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    root = tmp_path / "sources" / "portal"
    _run("git", "-C", str(root), "update-index", "--assume-unchanged", "runtime.py")
    (root / "runtime.py").write_text("ENABLED = False\n", encoding="utf-8")
    # A conventional Git diff incorrectly reports no changed paths.
    assert _run("git", "-C", str(root), "diff", "--name-only", "HEAD", "--") == ""
    _must_fail(lambda: build_attestation(tmp_path, spec))


def test_attestation_rejects_untracked_overlay_and_changed_git_pin(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    write_attestation(tmp_path, spec)
    overlay = tmp_path / "sources" / "portal" / "overlay.py"
    overlay.write_text("OVERRIDE = True\n", encoding="utf-8")
    _must_fail(lambda: verify_attestation(tmp_path, spec))
    overlay.unlink()
    changed = tuple(
        RuntimeSource(s.component, s.repository, s.ref,
                      "f" * 40 if s.component == "portal" else s.sha)
        for s in spec.sources
    )
    wrong_pin = InstallSpec(spec.schema, spec.install_id, changed)
    _must_fail(lambda: verify_attestation(tmp_path, wrong_pin))


def test_attestation_ignores_only_generated_cache_files(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    write_attestation(tmp_path, spec)
    cache = tmp_path / "sources" / "portal" / "__pycache__"
    cache.mkdir()
    (cache / "README.txt").write_text("harmless cache metadata", encoding="utf-8")
    assert verify_attestation(tmp_path, spec)["sha256"] == build_attestation(tmp_path, spec)["sha256"]


def test_attestation_rejects_executable_bytecode_in_excluded_cache(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    cache = tmp_path / "sources" / "portal" / "__pycache__"
    cache.mkdir()
    (cache / "runtime.cpython-312.pyc").write_bytes(b"arbitrary executable bytecode")
    _must_fail(lambda: write_attestation(tmp_path, spec))


def test_attestation_rejects_modified_receipt(tmp_path: Path) -> None:
    spec = _stage(tmp_path)
    write_attestation(tmp_path, spec)
    path = tmp_path / FILENAME
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["sha256"] = "0" * 64
    path.write_text(json.dumps(receipt), encoding="utf-8")
    _must_fail(lambda: verify_attestation(tmp_path, spec))
