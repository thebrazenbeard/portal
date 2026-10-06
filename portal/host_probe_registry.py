from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml

from .host_probe import PortalCommandHostProbe


_SCHEMA = "PORTAL_HOST_COMMAND_PROBES_V1"
_ALLOWED_TOP_LEVEL = {"schema", "probes"}
_ALLOWED_PROBE_KEYS = {
    "adapter_id",
    "command",
    "timeout_seconds",
    "cwd",
}


def load_host_command_probes(
    path: Path,
) -> dict[str, PortalCommandHostProbe]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("host probe manifest must be a mapping")
    if set(payload) != _ALLOWED_TOP_LEVEL:
        raise ValueError(
            "host probe manifest requires exactly schema and probes"
        )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported host probe manifest schema")

    raw_probes = payload["probes"]
    if not isinstance(raw_probes, list):
        raise ValueError("host probe manifest probes must be a list")
    if not raw_probes:
        raise ValueError("host probe manifest must contain at least one probe")

    result: dict[str, PortalCommandHostProbe] = {}
    for raw in raw_probes:
        if not isinstance(raw, Mapping):
            raise ValueError("host probe entry must be a mapping")
        unknown = set(raw) - _ALLOWED_PROBE_KEYS
        if unknown:
            raise ValueError(
                "host probe entry contains unsupported fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )

        adapter_id = raw.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise ValueError("host probe adapter_id is required")
        adapter_id = adapter_id.strip()
        if adapter_id in result:
            raise ValueError("duplicate host probe adapter_id")

        command = raw.get("command")
        if not isinstance(command, list):
            raise ValueError("host probe command must be a list")

        cwd = raw.get("cwd")
        if cwd is not None and (
            not isinstance(cwd, str) or not cwd.strip()
        ):
            raise ValueError("host probe cwd must be a non-empty string")

        result[adapter_id] = PortalCommandHostProbe(
            adapter_id=adapter_id,
            command=tuple(command),
            timeout_seconds=raw.get("timeout_seconds", 30.0),
            cwd=Path(cwd) if cwd is not None else None,
        )

    return dict(sorted(result.items()))
