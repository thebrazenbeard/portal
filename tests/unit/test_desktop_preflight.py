from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from portal.desktop_install import InstallSpec, RuntimeSource
from portal.desktop_preflight import inspect_staged_runtime


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(root: Path) -> tuple[InstallSpec, dict[str, object], str, str]:
    components = ("vera-mono", "portal", "pre-active", "volition")
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id="source-only-test",
        sources=tuple(
            RuntimeSource(name, f"thebrazenbeard/{name}", "main", f"{i + 1:040x}")
            for i, name in enumerate(components)
        ),
    )
    root.mkdir()
    for relative in (
        "sources", "state", "bridge/requests", "bridge/responses", "host", "logs",
    ):
        (root / relative).mkdir(parents=True)
    spec_path = root / "RUNTIME_INSTALL_SPEC.json"
    spec.write(spec_path)
    attestation: dict[str, object] = {
        "schema": "PORTAL_RUNTIME_SOURCE_ATTESTATION_V1",
        "install_id": spec.install_id,
        "sources": {},
        "sha256": "f" * 64,
    }
    att_path = root / "RUNTIME_SOURCE_ATTESTATION.json"
    att_path.write_text(json.dumps(attestation), encoding="utf-8")
    result = {
        "install_id": spec.install_id,
        "source_map": {
            source.component: {
                "repository": source.repository,
                "ref": source.ref,
                "sha": source.sha,
            }
            for source in spec.sources
        },
        "source_attestation": {
            "schema": attestation["schema"],
            "sha256": attestation["sha256"],
            "path": "RUNTIME_SOURCE_ATTESTATION.json",
        },
        "protected_effect_authority": False,
        "native_openai_router_replaced": False,
    }
    (root / "INSTALLATION_RESULT.json").write_text(json.dumps(result), encoding="utf-8")
    return spec, attestation, _digest(spec_path), _digest(att_path)


def _inspect(root: Path, att: dict[str, object], spec_sha: str, att_sha: str):
    return inspect_staged_runtime(
        root,
        expected_install_id="source-only-test",
        expected_spec_sha256=spec_sha,
        expected_attestation_sha256=att_sha,
        source_verifier=lambda *_: att,
    )


def test_source_only_candidate_does_not_call_host_or_write(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    # Fixture creation has ended: any write, process launch, socket or task
    # registration from the read-only inspector is a test failure.
    with (
        patch.object(Path, "write_text", side_effect=AssertionError("write forbidden")),
        patch.object(Path, "write_bytes", side_effect=AssertionError("write forbidden")),
        patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")),
        patch.object(socket.socket, "bind", side_effect=AssertionError("bind forbidden")),
    ):
        result = _inspect(root, att, spec_sha, att_sha)
    assert result["status"] == "SOURCE_PREFLIGHT_ONLY"
    assert result["source_bytes_verified"] is True
    assert result["release_qualified"] is False
    assert result["live_process_identity_verified"] is False
    assert result["scheduled_task_ownership_verified"] is False
    assert result["host_started_by_preflight"] is False
    assert result["activation_attempted_by_preflight"] is False


def test_wrong_spec_digest_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, _, att_sha = _fixture(root)
    result = _inspect(root, att, "0" * 64, att_sha)
    assert result["status"] == "HOLD"
    assert result["source_bytes_verified"] is False


def test_wrong_attestation_digest_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, _ = _fixture(root)
    assert _inspect(root, att, spec_sha, "0" * 64)["status"] == "HOLD"


def test_source_verifier_disagreement_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    assert _inspect(root, {**att, "sha256": "0" * 64}, spec_sha, att_sha)["status"] == "HOLD"


def test_install_id_disagreement_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    result = inspect_staged_runtime(
        root,
        expected_install_id="different-id",
        expected_spec_sha256=spec_sha,
        expected_attestation_sha256=att_sha,
        source_verifier=lambda *_: att,
    )
    assert result["status"] == "HOLD"


@pytest.mark.parametrize("field,value", [
    ("source_map", {}),
    ("protected_effect_authority", True),
    ("native_openai_router_replaced", True),
    ("source_attestation", {"sha256": "0" * 64}),
])
def test_result_receipt_mismatch_fails_closed(tmp_path, field, value):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    result_path = root / "INSTALLATION_RESULT.json"
    result = json.loads(result_path.read_text())
    result[field] = value
    result_path.write_text(json.dumps(result))
    assert _inspect(root, att, spec_sha, att_sha)["status"] == "HOLD"


def test_missing_installation_result_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    (root / "INSTALLATION_RESULT.json").unlink()
    assert _inspect(root, att, spec_sha, att_sha)["status"] == "HOLD"


def test_redirected_stage_subdirectory_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    (root / "bridge" / "responses").rmdir()
    (root / "bridge" / "responses").symlink_to(
        root / "state", target_is_directory=True,
    )
    result = _inspect(root, att, spec_sha, att_sha)
    assert result["status"] == "HOLD"
    assert any("bridge/responses" in issue for issue in result["blockers"])


def test_redirected_runtime_root_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    redirect = tmp_path / "redirect"
    redirect.symlink_to(root, target_is_directory=True)
    assert _inspect(redirect, att, spec_sha, att_sha)["status"] == "HOLD"


def test_redirected_attestation_fails_closed(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, _ = _fixture(root)
    att_path = root / "RUNTIME_SOURCE_ATTESTATION.json"
    original = tmp_path / "saved_attestation.json"
    att_path.rename(original)
    att_path.symlink_to(original)
    assert _inspect(root, att, spec_sha, _digest(original))["status"] == "HOLD"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not Windows DACLs")
def test_broad_posix_writes_are_flagged(tmp_path):
    root = tmp_path / "staged"
    _, att, spec_sha, att_sha = _fixture(root)
    root.chmod(root.stat().st_mode | stat.S_IWOTH)
    try:
        result = _inspect(root, att, spec_sha, att_sha)
        assert "RUNTIME_ROOT_GROUP_OR_WORLD_WRITABLE" in result["blockers"]
        assert result["status"] == "HOLD"
    finally:
        root.chmod(0o700)


def test_invalid_external_digest_rejected(tmp_path):
    with pytest.raises(ValueError):
        inspect_staged_runtime(
            tmp_path,
            expected_install_id="x",
            expected_spec_sha256="NOT_A_DIGEST",
            expected_attestation_sha256="0" * 64,
        )
