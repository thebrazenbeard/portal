from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import portal.cli as portal_cli


def _probe_manifest(tmp_path: Path) -> Path:
    probe_script = tmp_path / "probe.py"
    probe_script.write_text(
        "import json\n"
        "print(json.dumps({"
        "'schema':'PORTAL_HOST_PROBE_RESULT_V1',"
        "'adapter_id':'probe',"
        "'evidence_id':'probe:1',"
        "'routes':[],"
        "'occupancy':[{'node_id':'lappy','occupied_slots':0}]"
        "}))\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "probes.yaml"
    manifest.write_text(
        "schema: PORTAL_HOST_COMMAND_PROBES_V1\n"
        "probes:\n"
        "  - adapter_id: probe\n"
        f"    command: [{sys.executable!r}, {str(probe_script)!r}]\n",
        encoding="utf-8",
    )
    return manifest


def _authority_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "authority.yaml"
    path.write_text(
        "schema: PORTAL_HOST_AUTHORITY_V1\n"
        "grants: []\n",
        encoding="utf-8",
    )
    return path


def test_session_host_refresh_requires_probe_and_authority_pair(
    tmp_path: Path,
) -> None:
    args = SimpleNamespace(
        host_probes=_probe_manifest(tmp_path),
        host_authority=None,
        host_bridge=True,
        host_refresh_ttl_seconds=300.0,
        state_db=tmp_path / "portal.sqlite3",
    )

    with pytest.raises(
        ValueError,
        match="must be supplied together",
    ):
        portal_cli._session_host_refresh(args)


def test_session_host_refresh_requires_host_bridge(
    tmp_path: Path,
) -> None:
    args = SimpleNamespace(
        host_probes=_probe_manifest(tmp_path),
        host_authority=_authority_manifest(tmp_path),
        host_bridge=False,
        host_refresh_ttl_seconds=300.0,
        state_db=tmp_path / "portal.sqlite3",
    )

    with pytest.raises(
        ValueError,
        match="requires --host-bridge",
    ):
        portal_cli._session_host_refresh(args)


def test_host_probe_refresh_flags_require_command_session() -> None:
    args = SimpleNamespace(
        session_id=None,
        resume=False,
        worker_backends=None,
        nodes=None,
        host_probes=Path("probes.yaml"),
        host_authority=Path("authority.yaml"),
    )

    with pytest.raises(
        ValueError,
        match="--host-probes requires --session-id",
    ):
        portal_cli._run_payload(args)
