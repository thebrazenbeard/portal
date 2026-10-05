from __future__ import annotations

from pathlib import Path
import sys

import pytest

from portal.worker_registry import load_worker_backends


def test_load_process_worker_backend_manifest(tmp_path: Path) -> None:
    path = tmp_path / "backends.yaml"
    path.write_text(
        f"""schema: PORTAL_WORKER_BACKENDS_V1
workers:
  - node_id: alpha
    kind: PROCESS_JSON_V1
    command:
      - {sys.executable}
      - /opt/portal/worker.py
    timeout_seconds: 120
    pass_env:
      - OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    registry = load_worker_backends(path)

    assert set(registry) == {"alpha"}
    spec = registry["alpha"]
    assert spec.command[0] == sys.executable
    assert spec.timeout_seconds == 120
    assert spec.pass_env == ("OPENAI_API_KEY",)


def test_worker_backend_manifest_rejects_duplicate_nodes(tmp_path: Path) -> None:
    path = tmp_path / "backends.yaml"
    path.write_text(
        f"""schema: PORTAL_WORKER_BACKENDS_V1
workers:
  - node_id: alpha
    kind: PROCESS_JSON_V1
    command: [{sys.executable}]
  - node_id: alpha
    kind: PROCESS_JSON_V1
    command: [{sys.executable}]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate worker backend node_id"):
        load_worker_backends(path)


def test_worker_backend_manifest_rejects_unknown_backend_kind(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backends.yaml"
    path.write_text(
        f"""schema: PORTAL_WORKER_BACKENDS_V1
workers:
  - node_id: alpha
    kind: SHELL
    command: [{sys.executable}]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported worker backend kind"):
        load_worker_backends(path)
