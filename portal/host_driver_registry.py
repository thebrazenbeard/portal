from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml

from .host_command_driver import PortalCommandHostDriver


_SCHEMA = "PORTAL_HOST_COMMAND_DRIVERS_V1"
_ALLOWED_TOP_LEVEL = {"schema", "drivers"}
_ALLOWED_DRIVER_KEYS = {
    "adapter_id",
    "command",
    "timeout_seconds",
    "cwd",
}


def load_host_command_drivers(
    path: Path,
) -> dict[str, PortalCommandHostDriver]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("host driver manifest must be a mapping")
    if set(payload) != _ALLOWED_TOP_LEVEL:
        raise ValueError(
            "host driver manifest requires exactly schema and drivers"
        )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported host driver manifest schema")

    raw_drivers = payload["drivers"]
    if not isinstance(raw_drivers, list):
        raise ValueError("host driver manifest drivers must be a list")

    result: dict[str, PortalCommandHostDriver] = {}
    for raw in raw_drivers:
        if not isinstance(raw, Mapping):
            raise ValueError("host driver entry must be a mapping")
        unknown = set(raw) - _ALLOWED_DRIVER_KEYS
        if unknown:
            raise ValueError(
                "host driver entry contains unsupported fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )

        adapter_id = raw.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise ValueError("host driver adapter_id is required")
        adapter_id = adapter_id.strip()
        if adapter_id in result:
            raise ValueError("duplicate host driver adapter_id")

        command = raw.get("command")
        if not isinstance(command, list):
            raise ValueError("host driver command must be a list")
        if not command or any(
            not isinstance(item, str) or not item.strip()
            for item in command
        ):
            raise ValueError(
                "host driver command must contain non-empty strings"
            )

        cwd = raw.get("cwd")
        if cwd is not None and (
            not isinstance(cwd, str) or not cwd.strip()
        ):
            raise ValueError("host driver cwd must be a non-empty string")

        result[adapter_id] = PortalCommandHostDriver(
            command=tuple(command),
            timeout_seconds=raw.get("timeout_seconds", 300.0),
            cwd=Path(cwd) if cwd is not None else None,
        )

    return dict(sorted(result.items()))
