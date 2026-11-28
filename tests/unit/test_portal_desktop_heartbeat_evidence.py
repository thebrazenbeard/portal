"""Read-only evidence for a zeroed resident heartbeat, never recovery authority."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from portal.desktop_heartbeat_evidence import inspect_heartbeat_evidence
from portal.desktop_supervisor import RuntimeState, RuntimeSupervisor, RuntimeSupervisorConfig


def _bridge(root: Path) -> Path:
    bridge = root / "bridge"
    bridge.mkdir()
    return bridge


def test_exact_zeroed_incident_remains_blocked_without_file_mutation(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    heartbeat = bridge / "heartbeat.json"
    heartbeat.write_bytes(b"\x00" * 17_978)
    (bridge / "pid.txt").write_text("12345\n", encoding="utf-8")
    (bridge / "launch.json").write_text('{"pid": 12345}', encoding="utf-8")

    evidence = inspect_heartbeat_evidence(tmp_path)

    assert evidence["schema"] == "PORTAL_HEARTBEAT_EVIDENCE_V1"
    assert evidence["heartbeat"]["classification"] == "ZERO_FILLED"
    assert evidence["heartbeat"]["bytes_observed"] == 17_978
    assert evidence["pid_hint_present"] is True
    assert evidence["launch_reservation_present"] is True
    assert evidence["live_singleton_owner"] == "UNVERIFIED"
    assert evidence["recovery_authorized"] is False
    assert heartbeat.read_bytes() == b"\x00" * 17_978


def test_zeroed_incident_reaches_supervisor_block_without_launch(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    (bridge / "heartbeat.json").write_bytes(b"\x00" * 17_978)
    (bridge / "pid.txt").write_text("12345\n", encoding="utf-8")
    launches: list[tuple[Path, Path]] = []
    supervisor = RuntimeSupervisor(
        RuntimeSupervisorConfig(runtime_root=tmp_path),
        process_alive=lambda pid: False,
        launcher=lambda python, script: launches.append((python, script)) or 999,
    )

    status = supervisor.ensure_started(now=100.0)

    assert status.state is RuntimeState.BLOCKED
    assert status.reason == "heartbeat_invalid"
    assert launches == []


def test_missing_heartbeat_does_not_imply_no_live_owner(tmp_path: Path) -> None:
    evidence = inspect_heartbeat_evidence(tmp_path)
    assert evidence["heartbeat"]["classification"] == "MISSING"
    assert evidence["live_singleton_owner"] == "UNVERIFIED"
    assert evidence["recovery_authorized"] is False
    assert not (tmp_path / "bridge").exists()


def test_parseable_json_object_is_only_evidence_not_supervisor_acceptance(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    (bridge / "heartbeat.json").write_text(json.dumps({"pid": 77}), encoding="utf-8")
    evidence = inspect_heartbeat_evidence(tmp_path)
    assert evidence["heartbeat"]["classification"] == "JSON_OBJECT"
    assert evidence["live_singleton_owner"] == "UNVERIFIED"
    assert evidence["recovery_authorized"] is False


def test_invalid_and_oversized_content_have_distinct_evidence(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    heartbeat = bridge / "heartbeat.json"
    heartbeat.write_text("{", encoding="utf-8")
    assert inspect_heartbeat_evidence(tmp_path)["heartbeat"]["classification"] == "MALFORMED_JSON"
    heartbeat.write_bytes(b"a" * 65_537)
    evidence = inspect_heartbeat_evidence(tmp_path)
    assert evidence["heartbeat"]["classification"] == "OVERSIZED"
    assert evidence["heartbeat"]["bytes_observed"] == 65_537


def test_symlink_heartbeat_is_not_followed(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    outside = tmp_path / "sensitive.txt"
    outside.write_text("sensitive content", encoding="utf-8")
    try:
        (bridge / "heartbeat.json").symlink_to(outside)
    except OSError:
        pytest.skip("local user cannot create a symlink")
    evidence = inspect_heartbeat_evidence(tmp_path)
    assert evidence["heartbeat"]["classification"] == "SYMLINK_UNTRUSTED"
    assert evidence["heartbeat"]["bytes_observed"] is None
    assert outside.read_text(encoding="utf-8") == "sensitive content"


def test_symlinked_bridge_directory_is_not_followed(tmp_path: Path) -> None:
    external_bridge = tmp_path / "external-bridge"
    external_bridge.mkdir()
    (external_bridge / "heartbeat.json").write_text("{}", encoding="utf-8")
    root = tmp_path / "runtime"
    root.mkdir()
    try:
        (root / "bridge").symlink_to(external_bridge, target_is_directory=True)
    except OSError:
        pytest.skip("local user cannot create a directory symlink")

    evidence = inspect_heartbeat_evidence(root)

    assert evidence["heartbeat"]["classification"] == "SYMLINK_UNTRUSTED"
    assert evidence["heartbeat"]["bytes_observed"] is None
    assert evidence["pid_hint_present"] is None
    assert evidence["launch_reservation_present"] is None
    assert evidence["recovery_authorized"] is False


def test_swap_after_precheck_cannot_follow_symlink_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("POSIX directory-handle nofollow test")
    bridge = _bridge(tmp_path)
    heartbeat = bridge / "heartbeat.json"
    heartbeat.write_text("{}", encoding="utf-8")
    external = tmp_path / "outside.json"
    external.write_text('{"externally_swapped":true}', encoding="utf-8")
    original_path_open = Path.open
    original_os_open = os.open
    swapped = False

    def swap_target() -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            heartbeat.unlink()
            heartbeat.symlink_to(external)

    def path_open_with_swap(self: Path, *args: object, **kwargs: object):
        if self == heartbeat:
            swap_target()
        return original_path_open(self, *args, **kwargs)

    def os_open_with_swap(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None):
        if path == "heartbeat.json" and dir_fd is not None:
            swap_target()
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "open", path_open_with_swap)
    monkeypatch.setattr(os, "open", os_open_with_swap)

    evidence = inspect_heartbeat_evidence(tmp_path)

    assert swapped is True
    assert evidence["heartbeat"]["classification"] == "SYMLINK_UNTRUSTED"
    assert evidence["heartbeat"]["bytes_observed"] is None
    assert evidence["heartbeat"]["path_custody"] == "NOFOLLOW_COMPONENT_HANDLES"
    assert evidence["recovery_authorized"] is False
