"""Resident-side revalidation for an operator-pinned local process worker.

This narrows supervisor-to-host manifest drift. It does not establish the
independent origin of pins or make mutable executable paths race-free.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .portfolio_autopilot import _sha256_file, command_vector_sha256
from .worker_registry import load_worker_backends


_PIN_KEYS = (
    "expected_command_sha256",
    "expected_interpreter_sha256",
    "expected_worker_sha256",
)


def load_resident_backends(
    manifest_path: Path, *, nodes: tuple, pins: dict | None, live_auto: bool,
):
    """Return the same parsed backend objects that were checked at the host."""
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    backends = load_worker_backends(manifest_path)
    if manifest_path.read_bytes() != raw:
        raise ValueError("worker backend manifest changed during resident load")
    if not live_auto:
        return backends
    manifest = yaml.safe_load(raw.decode("utf-8"))
    workers = manifest["workers"]
    if not any(item.get("kind") == "PROCESS_JSON_V1" for item in workers):
        return backends
    if len(workers) != 1 or len(backends) != 1 or len(nodes) != 1:
        raise ValueError("resident local worker requires one worker and node")
    node = nodes[0]
    entry = workers[0]
    if (not node.enabled or node.max_parallel != 1
            or entry.get("node_id") != node.node_id
            or entry.get("kind") != "PROCESS_JSON_V1"):
        raise ValueError("resident local worker node is unqualified")
    configured = backends.get(node.node_id)
    if configured is None or configured.pass_env:
        raise ValueError("resident local worker must not inherit credentials")
    command = configured.command
    if (len(command) != 4
            or command[2] not in {"--checkout-index", "--checkout-root"}
            or not Path(command[3]).is_absolute()):
        raise ValueError("resident local worker requires pinned four-part argv")
    if not isinstance(pins, dict) or any(
        not isinstance(pins.get(k), str) or len(pins[k]) != 64
        or any(c not in "0123456789abcdef" for c in pins[k])
        for k in _PIN_KEYS
    ):
        raise ValueError("resident local worker requires explicit SHA-256 pins")
    interpreter, worker, repository_input = (
        Path(command[0]), Path(command[1]), Path(command[3])
    )
    if (not interpreter.is_absolute() or not interpreter.is_file()
            or interpreter.is_symlink()
            or worker.name != "local_ollama_worker.py"
            or not worker.is_absolute() or not worker.is_file()
            or worker.is_symlink()
            or repository_input.is_symlink()
            or (not repository_input.is_file()
                if command[2] == "--checkout-index"
                else not repository_input.is_dir())):
        raise ValueError("resident local worker path is unqualified")
    if command_vector_sha256(command) != pins["expected_command_sha256"]:
        raise ValueError("resident local worker command digest changed")
    if _sha256_file(interpreter) != pins["expected_interpreter_sha256"]:
        raise ValueError("resident local worker interpreter digest changed")
    if _sha256_file(worker) != pins["expected_worker_sha256"]:
        raise ValueError("resident local worker script digest changed")
    return backends
