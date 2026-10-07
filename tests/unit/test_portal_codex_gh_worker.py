from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from portal.codex_gh_worker import _proposal_files, run_codex_gh_proposal


def test_codex_gh_worker_builds_hash_bound_exact_head_proposal(
    tmp_path: Path,
) -> None:
    head = "a" * 40
    packet = {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "subject_id": "secret-frontier",
        "repository": "thebrazenbeard/secret",
        "source_ref": "main",
        "exact_head": head,
        "action": "EXECUTE_FRONTIER",
        "frontier": "Implement the next bounded source frontier.",
        "effect_ceiling": "SOURCE_ONLY",
        "advisory_only": True,
        "execution_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }
    packet_path = tmp_path / "packet.json"
    receipt_path = tmp_path / "receipt.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    calls: list[tuple[str, ...]] = []

    def runner(argv, **kwargs):
        argv = tuple(str(value) for value in argv)
        calls.append(argv)
        if argv[0] == "gh":
            checkout = Path(argv[4])
            checkout.mkdir(parents=True)
            (checkout / "src.txt").write_bytes(b"old\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "codex":
            checkout = Path(argv[argv.index("-C") + 1])
            (checkout / "src.txt").write_bytes(b"new\n")
            (checkout / "added.txt").write_bytes(b"added\n")
            return subprocess.CompletedProcess(argv, 0, "done", "")
        assert argv[0] == "git"
        if "fetch" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "checkout" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "status" in argv:
            return subprocess.CompletedProcess(
                argv, 0, " M src.txt\0?? added.txt\0", ""
            )
        target = argv[-1]
        if target in {"FETCH_HEAD", "HEAD"}:
            stdout = head + "\n"
        else:
            stdout = "b" * 40 + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    receipt = run_codex_gh_proposal(
        packet_path=packet_path,
        receipt_path=receipt_path,
        codex="codex",
        gh="gh",
        git="git",
        runner=runner,
    )

    assert receipt is not None
    assert receipt["receipt_class"] == "PROPOSED_SOURCE_TREE"
    assert receipt_path.exists()
    artifact = receipt["artifacts"][0]
    proposal_path = tmp_path / artifact["relative_path"]
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert proposal["repository"] == packet["repository"]
    assert proposal["source_ref"] == "main"
    assert proposal["expected_head"] == head
    files = {item["path"]: item for item in proposal["files"]}
    assert files["src.txt"]["content"] == "new\n"
    assert files["src.txt"]["expected_blob_sha"] == "b" * 40
    assert files["added.txt"]["expected_blob_sha"] is None
    codex_call = next(call for call in calls if call[0] == "codex")
    assert "workspace-write" in codex_call
    assert "never" in codex_call
    assert packet["frontier"] in codex_call[-1]


def test_codex_gh_worker_rejects_local_head_movement(tmp_path: Path) -> None:
    head = "a" * 40
    packet = {
        "schema": "PORTAL_WAVE_WORK_PACKET_V1",
        "subject_id": "head-move",
        "repository": "thebrazenbeard/secret",
        "source_ref": "main",
        "exact_head": head,
        "action": "EXECUTE_FRONTIER",
        "frontier": "Make one source proposal.",
        "effect_ceiling": "SOURCE_ONLY",
        "advisory_only": True,
        "execution_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
    }
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    def runner(argv, **kwargs):
        argv = tuple(str(value) for value in argv)
        if argv[0] == "gh":
            checkout = Path(argv[4])
            checkout.mkdir(parents=True)
            (checkout / "src.txt").write_bytes(b"old\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "codex":
            checkout = Path(argv[argv.index("-C") + 1])
            (checkout / "src.txt").write_bytes(b"new\n")
            return subprocess.CompletedProcess(argv, 0, "done", "")
        if "fetch" in argv or "checkout" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "status" in argv:
            return subprocess.CompletedProcess(argv, 0, " M src.txt\0", "")
        target = argv[-1]
        if target == "FETCH_HEAD":
            stdout = head + "\n"
        elif target == "HEAD":
            stdout = "c" * 40 + "\n"
        else:
            stdout = "b" * 40 + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    with pytest.raises(ValueError, match="local HEAD moved"):
        run_codex_gh_proposal(
            packet_path=packet_path,
            receipt_path=tmp_path / "receipt.json",
            codex="codex",
            gh="gh",
            git="git",
            runner=runner,
        )


def test_proposal_files_rejects_symlink_before_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "link.txt").write_bytes(b"linked\n")
    target = checkout / "target.txt"
    target.write_bytes(b"target\n")

    original_resolve = Path.resolve
    original_is_symlink = Path.is_symlink

    def fake_resolve(self, strict=False):
        if self.name == "link.txt":
            return original_resolve(target, strict=True)
        return original_resolve(self, strict=strict)

    def fake_is_symlink(self):
        if self.name == "link.txt":
            return True
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    with pytest.raises(ValueError, match="symlink"):
        _proposal_files(
            checkout=checkout,
            head="a" * 40,
            changes=(("??", "link.txt"),),
            git="git",
            runner=lambda *_args, **_kwargs: None,
            timeout_seconds=30.0,
        )
