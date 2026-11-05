"""Read-only evidence for a zeroed resident heartbeat, never recovery authority."""

from __future__ import annotations

import json
from pathlib import Path

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
    (bridge / "heartbeat.json").symlink_to(outside)
    evidence = inspect_heartbeat_evidence(tmp_path)
    assert evidence["heartbeat"]["classification"] == "SYMLINK_UNTRUSTED"
    assert evidence["heartbeat"]["bytes_observed"] is None
    assert outside.read_text(encoding="utf-8") == "sensitive content"
