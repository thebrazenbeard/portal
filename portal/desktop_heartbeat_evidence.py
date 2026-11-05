"""Read-only heartbeat triage; evidence is never a runtime recovery grant.

This intentionally does not inspect or acquire the Windows runtime mutex,
launch processes, rewrite bridge files, or clear the supervisor's BLOCKED state.
Even a dead PID hint cannot establish exclusive ownership of a host singleton.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_MAX_INSPECTION_BYTES = 64 * 1024


def _classify_heartbeat(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"classification": "SYMLINK_UNTRUSTED", "bytes_observed": None}
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_INSPECTION_BYTES + 1)
    except FileNotFoundError:
        return {"classification": "MISSING", "bytes_observed": None}
    except OSError:
        return {"classification": "UNREADABLE", "bytes_observed": None}

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
    return {"classification": classification, "bytes_observed": count}


def inspect_heartbeat_evidence(runtime_root: Path) -> dict[str, Any]:
    """Classify bounded file evidence without performing an ownership probe or writes.

    A launch reservation, PID hint, valid-looking JSON, or missing heartbeat is
    insufficient to prove process custody. No field from this function should
    authorize moving a file or starting a runtime.
    """
    bridge = Path(runtime_root) / "bridge"
    return {
        "schema": "PORTAL_HEARTBEAT_EVIDENCE_V1",
        "heartbeat": _classify_heartbeat(bridge / "heartbeat.json"),
        "pid_hint_present": (bridge / "pid.txt").exists(),
        "launch_reservation_present": (bridge / "launch.json").exists(),
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
