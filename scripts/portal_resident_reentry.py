"""Conservative resident re-entry for an explicitly seeded P.O.R.T.A.L. session.

A session must first be created by the governed 'portal run --session-id' path.
Each iteration independently reads the current control state, then resumes only
the exact stored session specification. This script never creates grants, routes,
work packets, or a new source-mutation execution mechanism.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Mapping, Sequence


def invoke_portal(arguments: Sequence[str]) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "portal.cli", *arguments],
        check=False, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Portal command failed (exit={result.returncode}): "
            + result.stderr.strip()[:1000]
        )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Portal command did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Portal command returned a non-object")
    return value


def reenter(
    *, session_id: str, state_db: Path, poll_seconds: float,
    max_polls: int, invoke: Callable[[Sequence[str]], Mapping[str, object]] = invoke_portal,
    sleep: Callable[[float], object] = time.sleep,
    emit: Callable[[dict[str, object]], object] | None = None,
) -> list[dict[str, object]]:
    """Poll and resume an existing durable session; fail closed on ambiguity.

    max_polls=0 is unbounded only when an operator explicitly runs the command.
    State is never reset after errors. A STOPPED session is never restarted.
    """
    if not session_id or not session_id.strip():
        raise ValueError("session_id is required")
    if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or poll_seconds < 1:
        raise ValueError("poll_seconds must be at least one second")
    if type(max_polls) is not int or max_polls < 0:
        raise ValueError("max_polls must be a nonnegative integer")
    db = Path(state_db).resolve()
    if not db.is_file():
        raise FileNotFoundError(f"preexisting session database required: {db}")
    common = ["--state-db", str(db), "--session-id", session_id.strip()]
    outcomes: deque[dict[str, object]] = deque(maxlen=100)
    count = 0
    while max_polls == 0 or count < max_polls:
        status = invoke(["status", *common])
        state = status.get("control_state")
        if state == "STOPPED":
            break
        if state != "RUNNING" or status.get("session_id") != session_id.strip():
            raise RuntimeError("resident refused ambiguous session identity/control state")
        # resume restores the target-bound non-secret spec from durable storage.
        result = invoke(["continue", "--resume", *common])
        if (result.get("session_id") != session_id.strip()
                or result.get("mode") != "PORTAL_COMMAND_SESSION_CONTINUE_V1"
                or result.get("control_state") not in ("RUNNING", "STOPPED")):
            raise RuntimeError("resident refused ambiguous continue result")
        outcome = {
            "session_id": session_id.strip(),
            "generation": result.get("generation"),
            "control_state": result.get("control_state"),
            "summary": result.get("summary"),
        }
        outcomes.append(outcome)
        if emit is not None:
            emit(outcome)
        count += 1
        if result.get("control_state") == "STOPPED" or count == max_polls:
            break
        sleep(float(poll_seconds))
    return list(outcomes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll an existing Portal session without broadening its authority")
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--poll-seconds", type=float, default=60)
    parser.add_argument("--max-polls", type=int, default=0,
                        help="0 means resident/unbounded; positive for a bounded test")
    args = parser.parse_args(argv)
    try:
        results = reenter(session_id=args.session_id, state_db=args.state_db,
                          poll_seconds=args.poll_seconds, max_polls=args.max_polls,
                          emit=lambda outcome: print(json.dumps(outcome, sort_keys=True), flush=True))
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"portal-resident-reentry: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"schema": "PORTAL_RESIDENT_REENTRY_V1",
                      "retained_polls": len(results), "recent_results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
