from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from portal import codex_gh_worker


def test_timed_out_child_is_outcome_unknown_and_redacts_command(
    tmp_path: Path, monkeypatch
) -> None:
    receipt_path = tmp_path / "receipt.json"

    def timed_out(**_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["codex", "private-frontier-do-not-persist"],
            timeout=7,
        )

    monkeypatch.setattr(codex_gh_worker, "run_codex_gh_proposal", timed_out)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex_gh_worker",
            "--codex", "codex",
            "--gh", "gh",
            "--git", "git",
            "--portal-packet", str(tmp_path / "packet.json"),
            "--portal-receipt", str(receipt_path),
        ],
    )

    assert codex_gh_worker.main() == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "PORTAL_WORKER_RECEIPT_V1"
    assert receipt["receipt_class"] == "OUTCOME_UNKNOWN"
    assert "timed out" in receipt["reason"]
    assert "private-frontier-do-not-persist" not in receipt["reason"]
    assert receipt["artifacts"] == []
