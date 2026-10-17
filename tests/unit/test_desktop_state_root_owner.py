"""Source-only Windows state-root ownership regressions for PORTAL Desktop."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

import pytest

from portal import desktop_host as host

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows local mutex boundary")


def test_same_state_root_blocks_second_owner_and_other_root_is_independent(tmp_path):
    root_a = tmp_path / "one" / "state" / "vera"
    root_b = tmp_path / "two" / "state" / "vera"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    assert host._windows_state_mutex_name(root_a) != host._windows_state_mutex_name(root_b)
    first = host._acquire_state_mutex(root_a)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            host._acquire_state_mutex(root_a)
        independent = host._acquire_state_mutex(root_b)
        host._release_state_mutex(independent)
    finally:
        host._release_state_mutex(first)
    released = host._acquire_state_mutex(root_a)
    host._release_state_mutex(released)


def test_failed_host_composition_releases_state_claim(tmp_path, monkeypatch):
    class DeliberateAbort(Exception):
        pass
    def fail(_root):
        raise DeliberateAbort
    monkeypatch.setattr(host, "_run_host_after_state_claim", fail)
    with pytest.raises(DeliberateAbort):
        host.run_host(tmp_path / "desktop")
    state_root = tmp_path / "desktop" / "state" / "vera"
    handle = host._acquire_state_mutex(state_root)
    host._release_state_mutex(handle)


def test_live_other_process_fails_closed_without_stealing_claim(tmp_path):
    state_root = tmp_path / "actual" / "state" / "vera"
    state_root.mkdir(parents=True)
    code = (
        "from pathlib import Path; import sys; "
        "from portal.desktop_host import _acquire_state_mutex,_release_state_mutex; "
        "p=Path(sys.argv[1]); "
        "exec('try:\\n h=_acquire_state_mutex(p)\\nexcept RuntimeError:\\n sys.exit(19)\\n'); "
        "_release_state_mutex(h)"
    )
    project_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=project_root)
    def child():
        return subprocess.run([sys.executable, "-c", code, str(state_root)],
                              env=env, capture_output=True, text=True, timeout=15)
    owner = host._acquire_state_mutex(state_root)
    try:
        conflict = child()
        assert conflict.returncode == 19, (conflict.stdout, conflict.stderr)
    finally:
        host._release_state_mutex(owner)
    allowed = child()
    assert allowed.returncode == 0, (allowed.stdout, allowed.stderr)


def test_different_package_source_versions_cannot_own_same_root(tmp_path):
    import json

    root = tmp_path / "desktop"
    state_root = host.prepare_runtime_layout(root)["state_root"] / "vera"
    version_a = {"sources": [{"component": "portal", "sha": "a" * 40}]}
    version_b = {"sources": [{"component": "portal", "sha": "b" * 40}]}
    assert host._runtime_id(version_a) != host._runtime_id(version_b)
    owner = host._acquire_state_mutex(state_root)
    try:
        (root / "RUNTIME_INSTALL_SPEC.json").write_text(json.dumps(version_b))
        code = (
            "import sys;from pathlib import Path;from portal import desktop_host as h\n"
            "h._run_host_after_state_claim=lambda root:0\n"
            "try:h.run_host(Path(sys.argv[1]))\n"
            "except RuntimeError as e:\n"
            " print(e);sys.exit(19)\n"
        )
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        other = subprocess.run([sys.executable, "-B", "-c", code, str(root)],
                               env=env, text=True, capture_output=True, timeout=15)
        assert other.returncode == 19, (other.stdout, other.stderr)
        assert "already owned" in other.stdout
    finally:
        host._release_state_mutex(owner)
