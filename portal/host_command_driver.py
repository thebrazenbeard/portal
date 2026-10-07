from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

from .host_pump import PortalHostDriverResult


_REQUEST_SCHEMA = "PORTAL_HOST_DRIVER_REQUEST_V1"
_RESULT_SCHEMA = "PORTAL_HOST_DRIVER_RESULT_V1"
_ECHO_FIELDS = (
    "dispatch_id",
    "adapter_id",
    "route_id",
    "subject_kind",
    "subject_id",
)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


class PortalCommandHostDriver:
    """Execute one already-bound host dispatch through a strict argv command.

    This is an execution transport only. Admission, route selection, target
    binding and authority checks have already happened before this driver runs.
    Process success alone never implies semantic completion: the wrapper must
    return a schema-bound result that echoes the exact attempted dispatch.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        timeout_seconds: float = 300.0,
        cwd: Path | None = None,
    ) -> None:
        if isinstance(command, (str, bytes)):
            raise ValueError("command must be a non-empty argv sequence")
        argv = tuple(command)
        if not argv or any(
            not isinstance(part, str) or not part.strip()
            for part in argv
        ):
            raise ValueError("command must contain non-empty argv strings")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or float(timeout_seconds) <= 0
        ):
            raise ValueError("timeout must be positive")

        self.command = tuple(part.strip() for part in argv)
        self.timeout_seconds = float(timeout_seconds)
        self.cwd = Path(cwd) if cwd is not None else None

    def execute(
        self,
        dispatch: Mapping[str, object],
        *,
        attempt_id: str,
    ) -> PortalHostDriverResult:
        attempt_id = _required_text(attempt_id, "attempt_id")
        bound = {
            field: _required_text(dispatch.get(field), field)
            for field in _ECHO_FIELDS
        }

        request_payload = {
            "schema": _REQUEST_SCHEMA,
            "attempt_id": attempt_id,
            "dispatch": dict(dispatch),
        }
        request_text = json.dumps(
            request_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

        completed = subprocess.run(
            self.command,
            input=request_text,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            cwd=self.cwd,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "host driver exited with exit code "
                f"{completed.returncode}"
            )

        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "host driver result must be valid JSON"
            ) from exc
        if not isinstance(response, dict):
            raise ValueError("host driver result must be a JSON object")
        if response.get("schema") != _RESULT_SCHEMA:
            raise ValueError(
                f"host driver result schema must be {_RESULT_SCHEMA}"
            )

        observed_attempt = _required_text(
            response.get("attempt_id"),
            "driver result attempt_id",
        )
        if observed_attempt != attempt_id:
            raise ValueError("driver result attempt_id does not match attempt")

        for field, expected in bound.items():
            observed = _required_text(
                response.get(field),
                f"driver result {field}",
            )
            if observed != expected:
                raise ValueError(
                    f"driver result {field} does not match bound dispatch"
                )

        state = _required_text(
            response.get("state"),
            "driver result state",
        )
        evidence_id = _required_text(
            response.get("evidence_id"),
            "driver result evidence_id",
        )
        return PortalHostDriverResult(
            state=state,
            evidence_id=evidence_id,
        )
