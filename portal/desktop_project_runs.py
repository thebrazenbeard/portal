"""Exact-source, locally bounded per-project Windows launch profiles.

No arbitrary shell commands, no profile-defined Python module, no implicit
provider authority, no claim to sandbox isolation. Each profile is a narrowly
reviewed known workload, and receipts preserve custody across GUI restarts.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from .desktop_portfolio import inspect_repository_head

_SCHEMA = "PORTAL_DESKTOP_RUN_PROFILE_V1"
_RECEIPT_SCHEMA = "PORTAL_DESKTOP_PROJECT_RUN_RECEIPT_V1"
_REPO = "thebrazenbeard/physica"
_PROFILE_ID = "physica-oscillator-smoke"
_ALLOWED_ARGS = (
    "simulate", "oscillator", "--initial", "1", "0",
    "--t1", "0.1", "--dt", "0.01", "--summary-only",
)
_EXPECTED_HEAD_PATTERN = re.compile(r"[0-9a-f]{40}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


def load_project_run_profile(source_root: Path, repository: str) -> dict[str, object] | None:
    if repository != _REPO:
        return None
    path = Path(source_root) / "registry" / "desktop_run_profiles_v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != _SCHEMA:
        raise ValueError("invalid Desktop run profile schema")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("unexpected profile catalogue")
    profile = profiles[0]
    if (
        not isinstance(profile, dict)
        or profile.get("id") != _PROFILE_ID
        or profile.get("repository") != _REPO
        or type(profile.get("local_simulation_only")) is not bool
        or profile["local_simulation_only"] is not True
        or profile.get("module") != "physica"
        or tuple(profile.get("args", [])) != _ALLOWED_ARGS
        or not isinstance(profile.get("expected_head"), str)
        or not _EXPECTED_HEAD_PATTERN.fullmatch(profile["expected_head"])
    ):
        raise ValueError("unapproved Desktop project run profile")
    return profile


def _record(path: Path, value: dict[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    if temp.exists():
        raise FileExistsError(f"partial receipt exists: {temp}")
    with temp.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(temp, path)


def _run_argv(argv: list[str], *, cwd: Path, timeout: float,
              env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, timeout=timeout, env=env,
        shell=False, check=False, capture_output=True, text=True,
    )


def _pinned_checkout(repository: str, head: str, cache: Path) -> Path:
    path = cache / f"physica-{head[:12]}"
    if not path.is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        stage = cache / f".incoming-{uuid.uuid4().hex}"
        try:
            cloned = _run_argv(
                ["git", "clone", "--quiet", "--no-checkout", "--single-branch",
                 f"https://github.com/{repository}.git", str(stage)],
                cwd=cache, timeout=65.0,
            )
            if cloned.returncode:
                raise RuntimeError("project source clone failed: " + cloned.stderr[:240])
            detached = _run_argv(
                ["git", "checkout", "--quiet", "--detach", head],
                cwd=stage, timeout=25.0,
            )
            if detached.returncode:
                raise RuntimeError("exact project checkout failed: " + detached.stderr[:240])
            stage.rename(path)
        finally:
            if stage.exists():
                import shutil
                shutil.rmtree(stage)
    observed = _run_argv(["git", "rev-parse", "HEAD"], cwd=path, timeout=8.0)
    if observed.returncode or observed.stdout.strip() != head:
        raise RuntimeError("cached project checkout is not pinned to expected source")
    dirty = _run_argv(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=path, timeout=8.0,
    )
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError("pinned project checkout is modified; refusing execution")
    if not (path / "src" / "physica" / "__main__.py").is_file():
        raise RuntimeError("source lacks expected Physica entrypoint")
    return path


def run_selected_local_project(
    repository: str,
    source_root: Path,
    runtime_root: Path,
    *,
    timeout_seconds: float = 25.0,
) -> dict[str, object]:
    """Run one finite Physica simulation, with exact ref and durable receipt.

    This is *not* an arbitrary project runner; no other repository has an
    eligible profile. No automatic retries after an uncertain process result.
    """
    profile = load_project_run_profile(source_root, repository)
    if profile is None:
        raise ValueError("no qualified local execution profile for this repository")
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("unapproved runtime budget")

    head = str(profile["expected_head"])
    live = inspect_repository_head(repository)
    if live != head:
        raise RuntimeError(
            "project main changed since profile review; execution held "
            f"(expected {head[:12]}, observed {live[:12]})"
        )

    state_root = Path(runtime_root) / "state" / "portal" / "project-runs"
    checkout = _pinned_checkout(repository, head, state_root / "sources")
    run_id = uuid.uuid4().hex
    run_dir = state_root / "receipts" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = run_dir / "receipt.json"
    attempt: dict[str, object] = {
        "schema": _RECEIPT_SCHEMA,
        "run_id": run_id,
        "repository": repository,
        "profile_id": _PROFILE_ID,
        "source_sha": head,
        "state": "ATTEMPTED",
        "started_at": time.time(),
        "declared_intent": "LOCAL_SIMULATION_ONLY",
        "os_sandboxed": False,
        "automatic_retry": False,
    }
    _record(receipt_path, attempt)
    # No inherited application credentials or remote-provider keys.
    env = {
        key: os.environ[key] for key in
        ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE")
        if key in os.environ
    }
    env.update({
        "PYTHONPATH": str(checkout / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    })
    argv = [sys.executable, "-B", "-m", "physica", *_ALLOWED_ARGS]
    try:
        completed = _run_argv(
            argv, cwd=run_dir, timeout=timeout_seconds, env=env,
        )
        stdout = completed.stdout
        if completed.returncode != 0:
            raise RuntimeError(
                f"physica exited {completed.returncode}: " + completed.stderr[:250]
            )
        if len(stdout) > 32768:
            raise RuntimeError("simulation receipt exceeds approved output size")
        result = json.loads(stdout)
        if (
            result.get("schema") != "physica.simulation.v1"
            or result.get("model") != "oscillator"
            or not _DIGEST_PATTERN.fullmatch(str(result.get("sha256", "")))
        ):
            raise RuntimeError("unqualified Physica simulation evidence")
        attempt.update({
            "state": "VERIFIED_COMPLETE",
            "completed_at": time.time(),
            "simulation_sha256": result["sha256"],
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "steps": result.get("steps"),
            "exit_code": completed.returncode,
        })
    except subprocess.TimeoutExpired:
        attempt.update({
            "state": "OUTCOME_UNKNOWN",
            "completed_at": time.time(),
            "error": "local project exceeded deadline; termination evidence unavailable",
        })
    except Exception as exc:
        attempt.update({
            "state": "VERIFIED_HELD",
            "completed_at": time.time(),
            "error": f"{type(exc).__name__}: {exc}"[:420],
        })

    _record(run_dir / "final.json", attempt)
    return {**attempt, "evidence_path": str(run_dir / "final.json")}


def list_project_run_receipts(
    runtime_root: Path, *, limit: int = 15, now: float | None = None,
) -> tuple[dict[str, object], ...]:
    """Read bounded local receipts without retrying or reconciling effects."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("receipt limit must be 1..50")
    clock = time.time() if now is None else float(now)
    root = Path(runtime_root) / "state" / "portal" / "project-runs" / "receipts"
    if not root.is_dir():
        return ()
    records: list[dict[str, object]] = []
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
            continue
        path = folder / "final.json"
        if not path.is_file():
            path = folder / "receipt.json"
        try:
            raw = path.read_text(encoding="utf-8")
            if len(raw) > 16_384:
                continue
            record = json.loads(raw)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("schema") != _RECEIPT_SCHEMA
            or record.get("run_id") != folder.name
            or record.get("repository") != _REPO
            or not isinstance(record.get("started_at"), (int, float))
        ):
            continue
        state = record.get("state")
        if state == "ATTEMPTED" and not (folder / "final.json").is_file():
            state = (
                "OUTCOME_UNKNOWN_NOT_FINALIZED"
                if clock - float(record["started_at"]) > 60
                else "ATTEMPTED_OR_INTERRUPTED"
            )
        if state not in {
            "VERIFIED_COMPLETE", "VERIFIED_HELD", "OUTCOME_UNKNOWN",
            "OUTCOME_UNKNOWN_NOT_FINALIZED", "ATTEMPTED_OR_INTERRUPTED",
        }:
            continue
        records.append({
            "run_id": folder.name,
            "repository": _REPO,
            "state": state,
            "started_at": float(record["started_at"]),
            "source_sha": str(record.get("source_sha", "")),
            "simulation_sha256": record.get("simulation_sha256"),
            "receipt_file": str(path),
        })
    records.sort(key=lambda item: (float(item["started_at"]), str(item["run_id"])), reverse=True)
    return tuple(records[:limit])
