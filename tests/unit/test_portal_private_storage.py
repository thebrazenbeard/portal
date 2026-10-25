from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat

import pytest

from portal.desktop_host import _atomic_json, prepare_runtime_layout
from portal.desktop_runtime import CognitionLedger
from portal.private_storage import ensure_private_directory


@contextmanager
def _umask(mode: int):
    previous = os.umask(mode)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode regression")
def test_cognition_and_bridge_response_custody_is_private_under_umask_022(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o755)
    root.chmod(0o755)  # Deliberately world-traversable installation root.
    with _umask(0o022):
        layout = prepare_runtime_layout(root)
        db = root / "state" / "cognition" / "desktop-cognition.sqlite3"
        ledger = CognitionLedger(db)
        try:
            _atomic_json(layout["responses"] / "answer.json", {
                "response_text": "private synthetic Rezon synthesis"
            })
            for directory in (
                root / "state", root / "state" / "cognition", root / "bridge",
                layout["requests"], layout["claimed"], layout["responses"],
            ):
                assert _mode(directory) == 0o700, directory
            assert _mode(db) == 0o600
            assert _mode(layout["responses"] / "answer.json") == 0o600
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(db) + suffix)
                if sidecar.exists():
                    assert _mode(sidecar) == 0o600
        finally:
            ledger.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode regression")
def test_existing_permissive_ledger_is_sealed_before_use(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    state = root / "state" / "cognition"
    response_dir = root / "bridge" / "responses"
    state.mkdir(parents=True)
    response_dir.mkdir(parents=True)
    state.chmod(0o755)
    response_dir.chmod(0o755)
    db = state / "desktop-cognition.sqlite3"
    old_response = response_dir / "existing.json"
    old_response.write_text('{"response_text":"previous private content"}', encoding="utf-8")
    old_response.chmod(0o644)
    with _umask(0o022), sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS legacy_marker(value TEXT)")
    db.chmod(0o644)
    layout = prepare_runtime_layout(root)
    ledger = CognitionLedger(db)
    try:
        assert _mode(state) == 0o700
        assert _mode(response_dir) == 0o700
        assert _mode(db) == 0o600
        assert _mode(old_response) == 0o600
        assert ledger.connection.execute(
            "SELECT name FROM sqlite_master WHERE name='legacy_marker'"
        ).fetchone() is not None
        _atomic_json(layout["responses"] / "fresh.json", {"response_text": "private"})
        assert _mode(response_dir / "fresh.json") == 0o600
    finally:
        ledger.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink regression")
def test_symlink_targets_are_not_read_or_overwritten(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")

    leaf = tmp_path / "runtime" / "state" / "cognition"
    leaf.parent.mkdir(parents=True)
    leaf.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        prepare_runtime_layout(tmp_path / "runtime")
    assert sentinel.read_text(encoding="utf-8") == "untouched"

    safe_dir = tmp_path / "private"
    safe_dir.mkdir(mode=0o700)
    db_link = safe_dir / "desktop.sqlite3"
    db_link.symlink_to(sentinel)
    with pytest.raises((OSError, ValueError)):
        CognitionLedger(db_link)
    response_link = safe_dir / "answer.json"
    response_link.symlink_to(sentinel)
    with pytest.raises((OSError, ValueError)):
        _atomic_json(response_link, {"response_text": "must not escape"})
    assert sentinel.read_text(encoding="utf-8") == "untouched"


@pytest.mark.skipif(os.name != "posix", reason="POSIX SQLite sidecar symlink regression")
def test_preexisting_sqlite_wal_symlink_is_rejected_before_sqlite_opens(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cognition"
    root.mkdir(mode=0o700)
    db = root / "desktop.sqlite3"
    target = tmp_path / "outside-sentinel"
    target.write_bytes(b"private synthetic data" * 512)
    original = target.read_bytes()
    wal = Path(str(db) + "-wal")
    wal.symlink_to(target)

    with pytest.raises((OSError, ValueError)):
        CognitionLedger(db)

    assert wal.is_symlink()
    assert target.read_bytes() == original


def test_junctioned_sensitive_directory_is_rejected_by_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "sensitive"
    original = Path.is_junction
    monkeypatch.setattr(
        Path, "is_junction",
        lambda path: path == target or original(path),
    )
    with pytest.raises(ValueError, match="junction"):
        ensure_private_directory(target)
    assert not target.exists()
