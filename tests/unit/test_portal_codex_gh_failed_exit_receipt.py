from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from portal import codex_gh_worker


def _packet(tmp_path: Path) -> Path:
    packet = {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "subject_id": "safe-subject",
        "repository": "sample/secret",
        "source_ref": "main",
        "exact_head": "a" * 40,
        "action": "EXECUTE_FRONTIER",
        "frontier": "private-frontier-do-not-persist",
        "effect_ceiling": "SOURCE_ONLY",
        "advisory_only": True,
        "execution_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def _run_cli(tmp_path: Path, monkeypatch, runner) -> dict[str, object]:
    original = codex_gh_worker.run_codex_gh_proposal
    monkeypatch.setattr(
        codex_gh_worker,
        "run_codex_gh_proposal",
        lambda **kwargs: original(**kwargs, runner=runner),
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "codex_gh_worker",
            "--codex", "codex.cmd",
            "--gh", "gh.exe",
            "--git", "git.exe",
            "--portal-packet", str(_packet(tmp_path)),
            "--portal-receipt", str(tmp_path / "receipt.json"),
        ],
    )
    assert codex_gh_worker.main() == 0
    return json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))


def test_codex_nonzero_after_checkout_fails_closed_without_private_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(argv, **kwargs):
        argv = tuple(str(x) for x in argv)
        calls.append(argv)
        if argv[0] == "gh.exe":
            Path(argv[4]).mkdir()
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "codex.cmd":
            checkout = Path(argv[argv.index("-C") + 1])
            (checkout / "partial.txt").write_text("partial", encoding="utf-8")
            return subprocess.CompletedProcess(
                argv, 1, "",
                "windows sandbox failed: CreateProcessWithLogonW failed: 267 "
                "private-frontier-do-not-persist",
            )
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    receipt = _run_cli(tmp_path, monkeypatch, runner)
    assert receipt["receipt_class"] == "OUTCOME_UNKNOWN"
    assert "SANDBOX_CREATE_PROCESS" in receipt["reason"]
    assert "inspect before retry" in receipt["reason"]
    assert "private-frontier-do-not-persist" not in receipt["reason"]
    assert receipt["artifacts"] == []
    assert (tmp_path / "checkout" / "partial.txt").exists()
    assert sum(call[0] == "codex.cmd" for call in calls) == 1


def test_failed_pre_codex_command_preserves_retryable_class_but_redacts_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 4, "", "private-frontier-do-not-persist",
        )

    receipt = _run_cli(tmp_path, monkeypatch, runner)
    assert receipt["receipt_class"] == "FAILED_RETRYABLE"
    assert "exit 4" in receipt["reason"]
    assert "private-frontier-do-not-persist" not in receipt["reason"]
    assert receipt["artifacts"] == []


def test_failed_post_codex_git_probe_is_unknown(tmp_path: Path, monkeypatch) -> None:
    def runner(argv, **kwargs):
        argv = tuple(str(x) for x in argv)
        if argv[0] == "gh.exe":
            Path(argv[4]).mkdir()
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "rev-parse" in argv:
            if argv[-1] == "HEAD":
                return subprocess.CompletedProcess(
                    argv, 1, "", "private-frontier-do-not-persist",
                )
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    receipt = _run_cli(tmp_path, monkeypatch, runner)
    assert receipt["receipt_class"] == "OUTCOME_UNKNOWN"
    assert "post_codex" in receipt["reason"]
    assert "private-frontier-do-not-persist" not in receipt["reason"]


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("read ACL run had errors", "SANDBOX_READ_ACL"),
        ("windows sandbox failed: CreateProcessWithLogonW failed: 267", "SANDBOX_CREATE_PROCESS"),
        ("windows sandbox failed", "SANDBOX_FAILURE"),
        ("secret content, no actionable error", "UNCLASSIFIED"),
    ],
)
def test_subprocess_diagnostics_are_static_allowlisted_labels(stderr: str, expected: str) -> None:
    assert codex_gh_worker._diagnostic_code(stderr) == expected
