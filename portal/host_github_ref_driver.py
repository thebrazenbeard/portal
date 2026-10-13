"""Exact-target, read-only GitHub ref verification driver for P.O.R.T.A.L.

Use through PortalCommandHostDriver after a durable host attempt has been
recorded. This module cannot grant its own authority or perform source writes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Mapping

_REQUEST = "PORTAL_HOST_DRIVER_REQUEST_V1"
_RESULT = "PORTAL_HOST_DRIVER_RESULT_V1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ECHO = ("dispatch_id", "adapter_id", "route_id", "subject_kind", "subject_id")
_MAX_INPUT_BYTES = 256 * 1024


class ReadOnlyDriverError(ValueError):
    """Reject an unqualified request rather than fabricating verification."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReadOnlyDriverError("duplicate JSON key")
        result[key] = value
    return result


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadOnlyDriverError(f"{field} is required")
    return value.strip()


def decode_request(data: bytes) -> tuple[str, dict[str, object]]:
    if not data or len(data) > _MAX_INPUT_BYTES:
        raise ReadOnlyDriverError("invalid request size")
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReadOnlyDriverError("invalid request JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _REQUEST:
        raise ReadOnlyDriverError("invalid request schema")
    attempt_id = _required(payload.get("attempt_id"), "attempt_id")
    dispatch = payload.get("dispatch")
    if not isinstance(dispatch, dict):
        raise ReadOnlyDriverError("dispatch object is required")
    for field in _ECHO:
        _required(dispatch.get(field), field)
    return attempt_id, dispatch


def read_live_head(repository: str, ref: str) -> str:
    """Read only a GitHub ref. No shell, git push, or mutations."""
    request = f"repos/{repository}/git/ref/heads/{ref}"
    try:
        p = subprocess.run(
            ["gh", "api", request],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadOnlyDriverError("GitHub ref transport unavailable") from exc
    if p.returncode:
        raise ReadOnlyDriverError("GitHub ref lookup failed")
    try:
        payload = json.loads(p.stdout)
    except (ValueError, TypeError) as exc:
        raise ReadOnlyDriverError("invalid GitHub ref response") from exc
    if not isinstance(payload, dict) or payload.get("ref") != f"refs/heads/{ref}":
        raise ReadOnlyDriverError("GitHub ref response does not match requested branch")
    value = payload.get("object")
    if not isinstance(value, dict) or value.get("type") != "commit":
        raise ReadOnlyDriverError("GitHub ref must resolve to a commit")
    observed = value.get("sha")
    if not isinstance(observed, str) or not _SHA40.fullmatch(observed):
        raise ReadOnlyDriverError("invalid GitHub ref head")
    return observed


def verify_dispatch(
    *,
    attempt_id: str,
    dispatch: Mapping[str, object],
    allowed_repositories: frozenset[str],
    required_adapter_id: str,
    required_node_id: str,
    required_ref: str = "main",
) -> dict[str, str]:
    repository = _required(dispatch.get("repository"), "repository")
    expected = _required(dispatch.get("exact_head"), "exact_head")
    adapter = _required(dispatch.get("adapter_id"), "adapter_id")
    ref = _required(dispatch.get("ref"), "ref")
    subject = _required(dispatch.get("subject_id"), "subject_id")
    route = _required(dispatch.get("route_id"), "route_id")
    node = _required(dispatch.get("node_id"), "node_id")
    if repository not in allowed_repositories:
        raise ReadOnlyDriverError("repository not allowlisted")
    if not _REPOSITORY.fullmatch(repository):
        raise ReadOnlyDriverError("invalid repository name")
    if dispatch.get("subject_kind") != "repository":
        raise ReadOnlyDriverError("not a repository subject")
    if subject != repository.split("/", 1)[1]:
        raise ReadOnlyDriverError("subject/repository mismatch")
    if adapter != required_adapter_id or route != f"local-gh:{subject}:{required_ref}":
        raise ReadOnlyDriverError("bound adapter or route mismatch")
    if node != required_node_id:
        raise ReadOnlyDriverError("bound node does not match configured host")
    if ref != required_ref or ref != "main":
        raise ReadOnlyDriverError("ref not allowed")
    if not _SHA40.fullmatch(expected):
        raise ReadOnlyDriverError("invalid exact head")
    if dispatch.get("action") != "VERIFY_EXACT_HEAD":
        raise ReadOnlyDriverError("unsupported work action")
    if dispatch.get("effect_ceiling") != "SOURCE_ONLY":
        raise ReadOnlyDriverError("unsupported effect ceiling")

    observed = read_live_head(repository, ref)
    response: dict[str, str] = {
        "schema": _RESULT,
        "attempt_id": attempt_id,
        "state": "VERIFIED_COMPLETE" if observed == expected else "VERIFIED_HELD",
        "evidence_id": f"github-readonly-ref:{repository}:{observed}",
    }
    for field in _ECHO:
        response[field] = _required(dispatch.get(field), field)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed-repository", action="append", required=True)
    parser.add_argument("--adapter-id", default="lappy-local-readonly-gh")
    parser.add_argument("--node-id", default="lappy")
    args = parser.parse_args(argv)
    allowlist = frozenset(args.allowed_repository)
    if not allowlist or any(not _REPOSITORY.fullmatch(x) for x in allowlist):
        parser.error("invalid repository allowlist")
    try:
        attempt, dispatch = decode_request(sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1))
        result = verify_dispatch(
            attempt_id=attempt,
            dispatch=dispatch,
            allowed_repositories=allowlist,
            required_adapter_id=args.adapter_id,
            required_node_id=args.node_id,
        )
    except ReadOnlyDriverError as exc:
        print(f"READONLY_HOST_DRIVER_HELD: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())