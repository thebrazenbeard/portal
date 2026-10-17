"""Read one GitHub repository ref through the existing no-authority host probe seam.

This source observation does NOT authorize GitHub writes or any host effect.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def _locator(repository: str, ref: str, expected_head: str) -> tuple[str, str, str]:
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise ValueError("repository must be owner/name")
    if not all(_NAME.fullmatch(part) and part not in {".", ".."}
               for part in repository.split("/")):
        raise ValueError("invalid GitHub repository locator")
    if (not isinstance(ref, str) or not _REF.fullmatch(ref)
            or ".." in ref
            or any(part.startswith(".") or part.endswith((".", ".lock"))
                   for part in ref.split("/"))):
        raise ValueError("invalid GitHub ref")
    if not isinstance(expected_head, str) or not _SHA.fullmatch(expected_head):
        raise ValueError("expected_head must be an exact lower-case commit SHA")
    return repository, ref, expected_head


def observe_head(repository: str, *, ref: str, expected_head: str) -> dict:
    """Return an observation-only, exact-currentness route; no effect capability."""
    repository, ref, expected_head = _locator(repository, ref, expected_head)
    try:
        completed = subprocess.run(
            ["gh", "api", "--method", "GET",
             f"repos/{repository}/commits/{ref}"],
            text=True, capture_output=True, timeout=15.0,
            shell=False, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("GitHub head observation unavailable") from exc
    if completed.returncode != 0:
        raise RuntimeError("GitHub head observation unavailable")
    try:
        reply = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError("GitHub head response is not JSON") from exc
    if not isinstance(reply, dict):
        raise ValueError("GitHub head response is not an object")
    observed = reply.get("sha")
    if not isinstance(observed, str) or not _SHA.fullmatch(observed):
        raise ValueError("GitHub head response lacks an exact commit SHA")

    return {
        "schema": "PORTAL_HOST_PROBE_RESULT_V1",
        "adapter_id": "github-head-observer",
        # Bind the observed byte-level head to the requested repository, ref,
        # and expected head. Equal SHAs across repos or stale expectations
        # are not interchangeable observation evidence.
        "evidence_id": f"github-head:{repository}:{ref}:{expected_head}:{observed}",
        "routes": [{
            "route_id": f"github-head:{repository}:{ref}",
            "node_id": "github-api",
            "target_kind": "repository",
            "target_id": repository,
            "capabilities": ["source_read"],
            "effect_capabilities": [],
            "available": True,
            "attached": True,
            "current": observed == expected_head,
            "preference": 0,
        }],
        "occupancy": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    result = observe_head(
        args.repository, ref=args.ref, expected_head=args.expected_head,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
