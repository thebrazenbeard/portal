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

def test_load_codex_gh_proposal_backend_manifest(tmp_path: Path) -> None:
    codex = (tmp_path / "codex").resolve()
    gh = (tmp_path / "gh").resolve()
    git = (tmp_path / "git").resolve()
    for executable in (codex, gh, git):
        executable.write_text("", encoding="utf-8")

    path = tmp_path / "backends.yaml"
    path.write_text(
        """schema: PORTAL_WORKER_BACKENDS_V1
workers:
  - node_id: lappy
    kind: CODEX_GH_PROPOSAL_V1
    codex: {codex}
    gh: {gh}
    git: {git}
    timeout_seconds: 600
""".format(
            codex=codex.as_posix(),
            gh=gh.as_posix(),
            git=git.as_posix(),
        ),
        encoding="utf-8",
    )

    registry = load_worker_backends(path)

    spec = registry["lappy"]
    assert spec.command[0] == sys.executable
    assert Path(spec.command[1]).name == "codex_gh_worker.py"
    assert Path(spec.command[1]).is_file()
    assert spec.command[spec.command.index("--codex") + 1] == str(codex)
    assert spec.command[spec.command.index("--gh") + 1] == str(gh)
    assert spec.command[spec.command.index("--git") + 1] == str(git)
    assert spec.timeout_seconds == 600

def test_process_backend_rejects_codex_specific_fields(tmp_path: Path) -> None:
    path = tmp_path / "backends.yaml"
    path.write_text(
        f"""schema: PORTAL_WORKER_BACKENDS_V1
workers:
  - node_id: alpha
    kind: PROCESS_JSON_V1
    command: [{sys.executable}]
    codex: /unexpected/codex
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="PROCESS_JSON_V1 does not accept codex, gh, or git",
    ):
        load_worker_backends(path)
