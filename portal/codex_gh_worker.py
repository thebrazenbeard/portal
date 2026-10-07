from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Callable, Mapping


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _checked(
    runner: Runner,
    argv: tuple[str, ...],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    result = runner(
        argv,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({Path(argv[0]).name}): exit {result.returncode}"
        )
    return result


def _packet(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Portal worker packet must be an object")
    if payload.get("schema") != "PORTAL_WAVE_WORK_PACKET_V1":
        raise ValueError("unexpected Portal worker packet schema")
    if payload.get("effect_ceiling") != "SOURCE_ONLY":
        raise ValueError("Codex proposal worker requires SOURCE_ONLY")
    if payload.get("advisory_only") is not True:
        raise ValueError("Codex proposal worker requires advisory_only")
    for key in (
        "execution_authorized",
        "source_mutation_authorized",
        "target_ref_mutation_authorized",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"Codex proposal worker requires {key}=false")
    for key in ("subject_id", "repository", "source_ref", "exact_head", "action"):
        if not isinstance(payload.get(key), str) or not str(payload[key]).strip():
            raise ValueError(f"Portal worker packet requires {key}")
    head = str(payload["exact_head"])
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise ValueError("Portal worker packet exact_head must be lowercase sha40")
    return payload


def _changed_paths(
    *,
    checkout: Path,
    git: str,
    runner: Runner,
    timeout_seconds: float,
) -> tuple[tuple[str, str], ...]:
    result = _checked(
        runner,
        (
            git,
            "-C",
            str(checkout),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ),
        timeout_seconds=timeout_seconds,
    )
    changed: list[tuple[str, str]] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("unsupported git status record")
        status = record[:2]
        if set(status).intersection("DRCTU"):
            raise ValueError("proposal may not delete, rename, copy, or type-change files")
        if status == "  ":
            continue
        changed.append((status, record[3:]))
    if not changed:
        raise ValueError("Codex produced no source proposal")
    return tuple(changed)


def _proposal_files(
    *,
    checkout: Path,
    head: str,
    changes: tuple[tuple[str, str], ...],
    git: str,
    runner: Runner,
    timeout_seconds: float,
) -> list[dict[str, object]]:
    root = checkout.resolve()
    files: list[dict[str, object]] = []
    for status, raw_path in changes:
        if "\\" in raw_path:
            raise ValueError("proposal paths must use forward slashes")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("proposal path is not canonical")
        path = pure.as_posix()
        candidate = checkout.joinpath(*pure.parts)
        if candidate.is_symlink():
            raise ValueError("proposal file escapes checkout or is a symlink")
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents:
            raise ValueError("proposal file escapes checkout or is a symlink")
        if not resolved.is_file():
            raise ValueError("proposal path must identify a regular file")
        raw = resolved.read_bytes()
        if b"\x00" in raw:
            raise ValueError("binary proposal files are not supported")
        try:
            content = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("proposal files must be UTF-8 text") from exc
        expected_blob_sha: str | None = None
        if status != "??" and "A" not in status:
            observed = _checked(
                runner,
                (git, "-C", str(checkout), "rev-parse", f"{head}:{path}"),
                timeout_seconds=timeout_seconds,
            ).stdout.strip()
            if len(observed) != 40 or any(
                ch not in "0123456789abcdef" for ch in observed
            ):
                raise ValueError("git returned invalid expected blob sha")
            expected_blob_sha = observed
        files.append(
            {
                "path": path,
                "content": content,
                "expected_blob_sha": expected_blob_sha,
            }
        )
    return files


def _prompt(packet: Mapping[str, object]) -> str:
    frontier = packet.get("frontier")
    frontier_text = (
        str(frontier).strip()
        if isinstance(frontier, str) and frontier.strip()
        else "Inspect the exact-head repository and make the smallest useful source proposal."
    )
    return (
        "You are a proposal-only coding worker for P.O.R.T.A.L. "
        "Work only inside this detached checkout at the exact supplied head. "
        "Implement the smallest coherent source-only change that advances the frontier. "
        "Do not commit, push, create or update remote branches, merge, deploy, install, "
        "change credentials or permissions, or perform destructive cleanup. "
        "Do not delete or rename files. Keep changed files UTF-8 text. "
        "Run local tests when useful, then stop with the working tree containing the proposal. "
        f"Action: {packet['action']}. Frontier: {frontier_text}"
    )


def run_codex_gh_proposal(
    *,
    packet_path: Path,
    receipt_path: Path,
    codex: str,
    gh: str,
    git: str,
    runner: Runner | None = None,
    timeout_seconds: float = 840.0,
) -> dict[str, object]:
    process_runner = runner or subprocess.run
    packet = _packet(Path(packet_path))
    receipt_path = Path(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    checkout = receipt_path.parent / "checkout"
    if checkout.exists():
        raise ValueError("proposal checkout already exists without a receipt")

    repository = str(packet["repository"])
    source_ref = str(packet["source_ref"])
    head = str(packet["exact_head"])
    _checked(
        process_runner,
        (
            gh,
            "repo",
            "clone",
            repository,
            str(checkout),
            "--",
            "--filter=blob:none",
            "--no-checkout",
        ),
        timeout_seconds=timeout_seconds,
    )
    _checked(
        process_runner,
        (git, "-C", str(checkout), "fetch", "--no-tags", "origin", source_ref),
        timeout_seconds=timeout_seconds,
    )
    observed_head = _checked(
        process_runner,
        (git, "-C", str(checkout), "rev-parse", "FETCH_HEAD"),
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    if observed_head != head:
        raise ValueError("source ref moved before proposal generation")
    _checked(
        process_runner,
        (git, "-C", str(checkout), "checkout", "--detach", head),
        timeout_seconds=timeout_seconds,
    )
    _checked(
        process_runner,
        (
            codex,
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "-C",
            str(checkout),
            "exec",
            _prompt(packet),
        ),
        timeout_seconds=timeout_seconds,
    )
    local_head = _checked(
        process_runner,
        (git, "-C", str(checkout), "rev-parse", "HEAD"),
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    if local_head != head:
        raise ValueError("local HEAD moved during proposal generation")

    changes = _changed_paths(
        checkout=checkout,
        git=git,
        runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    files = _proposal_files(
        checkout=checkout,
        head=head,
        changes=changes,
        git=git,
        runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    proposal = {
        "schema": "PORTAL_SOURCE_TREE_PROPOSAL_V1",
        "repository": repository,
        "source_ref": source_ref,
        "expected_head": head,
        "message": f"Portal: {packet['subject_id']}",
        "files": files,
    }
    proposal_path = receipt_path.parent / "proposal.json"
    _write_json(proposal_path, proposal)
    proposal_sha256 = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    receipt: dict[str, object] = {
        "schema": "PORTAL_WORKER_RECEIPT_V1",
        "receipt_class": "PROPOSED_SOURCE_TREE",
        "reason": "Codex produced an exact-head source-tree proposal",
        "artifacts": [
            {
                "kind": "SOURCE_TREE_PROPOSAL",
                "relative_path": "proposal.json",
                "sha256": proposal_sha256,
            }
        ],
    }
    _write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--gh", required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--portal-packet", type=Path, required=True)
    parser.add_argument("--portal-receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_codex_gh_proposal(
            packet_path=args.portal_packet,
            receipt_path=args.portal_receipt,
            codex=args.codex,
            gh=args.gh,
            git=args.git,
        )
    except ValueError as exc:
        _write_json(
            args.portal_receipt,
            {
                "schema": "PORTAL_WORKER_RECEIPT_V1",
                "receipt_class": "FAILED_DETERMINISTIC",
                "reason": f"{type(exc).__name__}: {exc}",
                "artifacts": [],
            },
        )
    except Exception as exc:
        _write_json(
            args.portal_receipt,
            {
                "schema": "PORTAL_WORKER_RECEIPT_V1",
                "receipt_class": "FAILED_RETRYABLE",
                "reason": f"{type(exc).__name__}: {exc}",
                "artifacts": [],
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
