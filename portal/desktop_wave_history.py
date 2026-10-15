"""Bounded, read-only view of existing PORTAL governed wave receipts.

This is local evidence reconciliation, not a project runner, provider
attestation, or a claim that queued logical lanes are real workers.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

_SHA = re.compile(r"[0-9a-f]{40}")
_SCHEMAS = frozenset({
    "PORTAL_PROJECT_RUNNER_89_FINAL_V1",
    "PORTAL_PROJECT_RUNNER_89_FINAL_72_LOGICAL_LANES_V1",
})


def _load(path: Path, *, ceiling: int = 32_768) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > ceiling:
        raise ValueError("missing or unqualified receipt")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("receipt must be an object")
    return value


def list_governed_wave_receipts(
    runtime_root: Path, *, limit: int = 10,
) -> tuple[dict[str, object], ...]:
    """Reconcile final receipts before reporting an inspection wave.

    No network, shell, mutation, retries, or arbitrary source execution.
    Reject incomplete waves and misleading counts instead of decorating
    them as successful. Local receipts are not independent attestation.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("wave limit must be 1..20")
    root = Path(runtime_root) / "state" / "portal" / "governed-waves"
    if root.is_symlink() or not root.is_dir():
        return ()
    rows: list[dict[str, object]] = []
    directories = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    for wave in directories[:60]:
        try:
            summary = _load(wave / "summary.json")
            manifest = _load(wave / "manifest.json")
            if summary.get("schema") not in _SCHEMAS:
                continue
            count = summary.get("scope_count")
            receipts = summary.get("receipts")
            workers = summary.get("max_concurrent_executions")
            slots = summary.get("max_logical_queue")
            if (type(count) is not int or not 1 <= count <= 300
                    or type(receipts) is not int or receipts != count
                    or type(workers) is not int or not 1 <= workers <= 8
                    or type(slots) is not int or not 1 <= slots <= 72
                    or manifest.get("execution_lane_limit") != workers
                    or manifest.get("logic_queue_limit") != slots
                    or summary.get("source_code_executed") != 0
                    or summary.get("repositories_modified") != 0
                    or summary.get("independent_logic_agents") != 0):
                continue
            sha = summary.get("runtime_source_sha")
            if (not isinstance(sha, str) or not _SHA.fullmatch(sha)
                    or manifest.get("selected_runner_sha") != sha):
                continue
            names = manifest.get("exact_live_names")
            if (not isinstance(names, list) or len(names) != count
                    or any(not isinstance(name, str) for name in names)
                    or len(set(names)) != count):
                continue
            subjects = wave / "subjects"
            if subjects.is_symlink() or not subjects.is_dir():
                continue
            folders = [p for p in subjects.iterdir() if p.is_dir() and not p.is_symlink()]
            if len(folders) != count:
                continue
            observed_names: set[str] = set()
            lane_ids: set[str] = set()
            all_current = True
            for folder in folders:
                final = _load(folder / "final.json")
                if not (folder / "attempt.json").is_file():
                    raise ValueError("missing attempt")
                name = final.get("repository")
                lane = final.get("lane")
                if (not isinstance(name, str) or name in observed_names
                        or not isinstance(lane, str) or not lane.strip()
                        or final.get("source_code_executed") is not False
                        or final.get("github_repository_mutated") is not False):
                    raise ValueError("unqualified final receipt")
                observed_names.add(name)
                lane_ids.add(lane)
                all_current = all_current and final.get("state") == "SOURCE_CURRENTNESS_VERIFIED"
            if observed_names != set(names):
                continue
            declared_lanes = summary.get("distinct_logical_lanes")
            if declared_lanes is not None and (
                type(declared_lanes) is not int or declared_lanes != len(lane_ids)
            ):
                continue
            rows.append({
                "wave": wave.name,
                "subjects": count,
                "inspected": receipts if all_current else 0,
                "logical_lanes": len(lane_ids),
                "logical_queue_limit": slots,
                "physical_workers_limit": workers,
                "projects_executed": 0,
                "repositories_modified": 0,
                "status": "LOCAL_INSPECTION_COMPLETE" if all_current else "LOCAL_RECEIPTS_HELD",
                "source_sha": sha,
                "evidence_directory": str(wave),
                "independently_attested": False,
            })
            if len(rows) >= limit:
                break
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return tuple(rows)
