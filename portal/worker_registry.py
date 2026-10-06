from __future__ import annotations

from pathlib import Path
import shutil
import sys
from typing import Mapping

import yaml

from .worker_backend import ProcessWorkerSpec


_SCHEMA = "PORTAL_WORKER_BACKENDS_V1"
_ALLOWED_TOP_LEVEL = {"schema", "workers"}
_ALLOWED_WORKER_KEYS = {
    "node_id",
    "kind",
    "command",
    "timeout_seconds",
    "pass_env",
    "codex",
    "gh",
    "git",
}


def _executable(raw: object, name: str) -> str:
    if raw is None:
        candidate = shutil.which(name)
        if candidate is None:
            raise ValueError(f"{name} executable is unavailable")
        return str(Path(candidate).resolve())
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} executable must be a non-empty string")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        raise ValueError(f"{name} executable must be an absolute path")
    if not candidate.is_file():
        raise ValueError(f"{name} executable is unavailable")
    return str(candidate.resolve())


def load_worker_backends(path: Path) -> dict[str, ProcessWorkerSpec]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("worker backend manifest must be a mapping")
    if set(payload) != _ALLOWED_TOP_LEVEL:
        raise ValueError(
            "worker backend manifest requires exactly schema and workers"
        )
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported worker backend manifest schema")
    raw_workers = payload["workers"]
    if not isinstance(raw_workers, list):
        raise ValueError("worker backend workers must be a list")

    result: dict[str, ProcessWorkerSpec] = {}
    for raw in raw_workers:
        if not isinstance(raw, Mapping):
            raise ValueError("worker backend entry must be a mapping")
        unknown = set(raw) - _ALLOWED_WORKER_KEYS
        if unknown:
            raise ValueError(
                "worker backend entry contains unsupported fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )
        node_id = raw.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("worker backend node_id is required")
        node_id = node_id.strip()
        if node_id in result:
            raise ValueError("duplicate worker backend node_id")
        kind = raw.get("kind")
        pass_env = raw.get("pass_env", [])
        if not isinstance(pass_env, list):
            raise ValueError("process worker backend pass_env must be a list")
        timeout_seconds = raw.get("timeout_seconds", 900.0)

        if kind == "PROCESS_JSON_V1":
            if any(raw.get(name) is not None for name in ("codex", "gh", "git")):
                raise ValueError(
                    "PROCESS_JSON_V1 does not accept codex, gh, or git"
                )
            command = raw.get("command")
            if not isinstance(command, list) or not command:
                raise ValueError("process worker backend command must be a list")
            if any(not isinstance(item, str) for item in command):
                raise ValueError(
                    "process worker backend command entries must be strings"
                )
        elif kind == "CODEX_GH_PROPOSAL_V1":
            if raw.get("command") is not None:
                raise ValueError(
                    "Codex GitHub proposal backend does not accept command"
                )
            command = (
                sys.executable,
                str(Path(__file__).with_name("codex_gh_worker.py").resolve()),
                "--codex",
                _executable(raw.get("codex"), "codex"),
                "--gh",
                _executable(raw.get("gh"), "gh"),
                "--git",
                _executable(raw.get("git"), "git"),
            )
        else:
            raise ValueError("unsupported worker backend kind")

        result[node_id] = ProcessWorkerSpec(
            command=tuple(command),
            timeout_seconds=timeout_seconds,
            pass_env=tuple(pass_env),
        )

    return dict(sorted(result.items()))
