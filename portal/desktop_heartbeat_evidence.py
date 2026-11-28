"""Read-only heartbeat triage; evidence is never a runtime recovery grant.

This intentionally does not inspect or acquire the Windows runtime mutex,
launch processes, rewrite bridge files, or clear the supervisor's BLOCKED state.
Even a dead PID hint cannot establish exclusive ownership of a host singleton.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import stat
from typing import Any


_MAX_INSPECTION_BYTES = 64 * 1024
_HAS_DIR_FD_OPEN = os.open in os.supports_dir_fd


class _UntrustedPath(Exception):
    """The read would traverse a symlink, reparse point, or non-directory."""


def _posix_bounded_nofollow_read(path: Path) -> bytes:
    """Open each component relative to a pinned parent descriptor.

    This prevents path-component symlinks and final-path swaps from redirecting
    the file read on POSIX. It is not a host identity or mount-custody proof.
    """
    components = path.parts[1:] if path.is_absolute() else path.parts
    if not components or any(part in {"", ".", ".."} for part in components):
        raise _UntrustedPath
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    with ExitStack() as stack:
        base = path.anchor if path.is_absolute() else "."
        descriptor = os.open(base, directory_flags)
        stack.callback(os.close, descriptor)
        try:
            for component in components[:-1]:
                descriptor = os.open(component, directory_flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
            leaf = os.open(components[-1], leaf_flags, dir_fd=descriptor)
            stack.callback(os.close, leaf)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise _UntrustedPath from exc
            raise
        if not stat.S_ISREG(os.fstat(leaf).st_mode):
            raise _UntrustedPath
        return os.read(leaf, _MAX_INSPECTION_BYTES + 1)


def _best_effort_bounded_read(path: Path) -> bytes:
    """Windows fallback: no guaranteed reparse-point or swap resistance."""
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise _UntrustedPath
    with path.open("rb") as stream:
        return stream.read(_MAX_INSPECTION_BYTES + 1)


def _classify_heartbeat(path: Path) -> dict[str, Any]:
    handle_nofollow = (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and _HAS_DIR_FD_OPEN
    )
    custody = "NOFOLLOW_COMPONENT_HANDLES" if handle_nofollow else "BEST_EFFORT_UNVERIFIED"
    try:
        raw = (
            _posix_bounded_nofollow_read(path)
            if handle_nofollow
            else _best_effort_bounded_read(path)
        )
    except _UntrustedPath:
        return {
            "classification": "SYMLINK_UNTRUSTED",
            "bytes_observed": None,
            "path_custody": custody,
        }
    except FileNotFoundError:
        return {"classification": "MISSING", "bytes_observed": None, "path_custody": custody}
    except OSError:
        return {"classification": "UNREADABLE", "bytes_observed": None, "path_custody": custody}

    count = len(raw)
    if count > _MAX_INSPECTION_BYTES:
        classification = "OVERSIZED"
    elif not raw:
        classification = "EMPTY"
    elif not any(raw):
        classification = "ZERO_FILLED"
    else:
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError):
            classification = "MALFORMED_JSON"
        else:
            # JSON_OBJECT does not imply acceptance by RuntimeSupervisor.
            classification = "JSON_OBJECT" if isinstance(document, dict) else "JSON_NON_OBJECT"
    return {"classification": classification, "bytes_observed": count, "path_custody": custody}


def inspect_heartbeat_evidence(runtime_root: Path) -> dict[str, Any]:
    """Classify bounded file evidence without performing an ownership probe or writes.

    A launch reservation, PID hint, valid-looking JSON, or missing heartbeat is
    insufficient to prove process custody. No field from this function should
    authorize moving a file or starting a runtime.
    """
    bridge = Path(runtime_root) / "bridge"
    heartbeat = _classify_heartbeat(bridge / "heartbeat.json")
    # A rejected path cannot establish even the existence of related hint files.
    untrusted = heartbeat["classification"] == "SYMLINK_UNTRUSTED"
    return {
        "schema": "PORTAL_HEARTBEAT_EVIDENCE_V1",
        "heartbeat": heartbeat,
        "pid_hint_present": None if untrusted else (bridge / "pid.txt").exists(),
        "launch_reservation_present": None if untrusted else (bridge / "launch.json").exists(),
        "live_singleton_owner": "UNVERIFIED",
        "recovery_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only desktop heartbeat evidence")
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(inspect_heartbeat_evidence(args.runtime_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
