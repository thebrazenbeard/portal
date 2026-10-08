"""WSL/Win32 worker workspace-locality boundary; no WSL environment required."""
from pathlib import Path
import json
import sys

import pytest
import yaml

from portal import worker_backend
from portal.worker_backend import ProcessWorkerSpec
from portal.worker_registry import load_worker_backends


@pytest.mark.parametrize(
    ("platform", "root", "reason"),
    [
        ("wsl", "/home/vera/repos", None),
        ("wsl", "/mnt/c/Users/patri/repos", "WSL worker"),
        ("wsl", "/mnt/d", "WSL worker"),
        ("linux", "/mnt/c/source", None),
        ("windows", "D:\\VERA\\workers", None),
        ("windows", r"\\wsl$\Ubuntu\home\vera\repo", "Windows worker"),
        ("windows", r"\\wsl.localhost\Ubuntu\home\vera\repo", "Windows worker"),
        ("windows", r"\\?\UNC\wsl$\Ubuntu\repo", "Windows worker"),
    ],
)
def test_platform_locality_classification(platform, root, reason):
    result = worker_backend._cross_os_workspace_reason(
        root, worker_platform=platform
    )
    if reason is None:
        assert result is None
    else:
        assert reason in result


def test_worker_rejects_cross_os_root_before_creating_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(
        worker_backend, "_host_worker_platform", lambda: "wsl"
    )
    # On Windows, test dispatch without a real WSL mount by overriding only
    # the cross-filesystem classifier at this boundary.
    cross = tmp_path / "mounted"
    original = worker_backend._cross_os_workspace_reason
    def classifier(path, *, worker_platform):
        if str(path).endswith("mounted"):
            return "WSL worker workspace uses Windows-mounted drive"
        return original(path, worker_platform=worker_platform)
    monkeypatch.setattr(worker_backend, "_cross_os_workspace_reason", classifier)
    pkt = {
        "advisory_only": True,
        "execution_authorized": False,
        "execution_effect_class": None,
        "execution_promotion": None,
        "protected_effects_authorized": False,
        "source_mutation_authorized": False,
        "target_ref_mutation_authorized": False,
        "run_id": "run",
        "subject_id": "target",
        "node_id": "wsl-node",
        "delivery_fencing_token": 1,
        "exact_head": "a" * 40,
        "plan_sha256": "b" * 64,
    }
    with pytest.raises(ValueError, match="WSL worker"):
        worker_backend.run_process_worker(
            packet=pkt,
            spec=ProcessWorkerSpec(command=(sys.executable,), timeout_seconds=3),
            workspace_root=cross,
        )
    assert not cross.exists()


def test_explicit_exception_must_be_boolean():
    with pytest.raises(ValueError, match="allow_cross_os_workspace"):
        ProcessWorkerSpec(
            command=(sys.executable,), allow_cross_os_workspace="true"
        )


def test_manifest_explicit_exception_roundtrips(tmp_path):
    manifest = {
        "schema": "PORTAL_WORKER_BACKENDS_V1",
        "workers": [
            {
                "node_id": "n1",
                "kind": "PROCESS_JSON_V1",
                "command": [sys.executable],
                "allow_cross_os_workspace": True,
            }
        ],
    }
    p = tmp_path / "workers.yaml"
    p.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    spec = load_worker_backends(p)["n1"]
    assert spec.allow_cross_os_workspace is True
    manifest["workers"][0]["allow_cross_os_workspace"] = "yes"
    p.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="allow_cross_os_workspace"):
        load_worker_backends(p)


def test_default_manifest_denies_cross_os_exception(tmp_path):
    manifest = {
        "schema": "PORTAL_WORKER_BACKENDS_V1",
        "workers": [{"node_id": "n1", "kind": "PROCESS_JSON_V1",
                     "command": [sys.executable]}],
    }
    p = tmp_path / "workers.yaml"
    p.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert load_worker_backends(p)["n1"].allow_cross_os_workspace is False


def test_forward_slash_unc_path_is_rejected():
    reason = worker_backend._cross_os_workspace_reason(
        "//wsl$/Ubuntu/home/vera/repo", worker_platform="windows"
    )
    assert reason and "Windows worker" in reason


def test_windows_unc_is_rejected_without_resolving_network_path(monkeypatch):
    monkeypatch.setattr(
        worker_backend, "_host_worker_platform", lambda: "windows"
    )
    original = Path.resolve

    def guarded_resolve(self, *args, **kwargs):
        if str(self).lower().startswith("\\\\wsl$"):
            raise AssertionError("UNC network path must not be resolved")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    with pytest.raises(ValueError, match="Windows worker"):
        worker_backend.validate_process_worker_workspace_locality(
            Path(r"\\wsl$\Ubuntu\home\vera\repo")
        )


def test_explicit_operator_exception_avoids_cross_os_denial(monkeypatch):
    monkeypatch.setattr(
        worker_backend, "_host_worker_platform", lambda: "windows"
    )
    worker_backend.validate_process_worker_workspace_locality(
        Path(r"\\wsl$\Ubuntu\home\vera\repo"),
        allow_cross_os_workspace=True,
    )


def test_adapter_rejects_cross_os_workspace_before_dispatch(tmp_path, monkeypatch):
    from portal.process_adapter import PortalProposalProcessAdapter
    monkeypatch.setattr(
        worker_backend, "_host_worker_platform", lambda: "windows"
    )
    with pytest.raises(ValueError, match="Windows worker"):
        PortalProposalProcessAdapter(
            state_db=tmp_path / "not-created.sqlite",
            nodes=(),
            backends={"node": ProcessWorkerSpec(command=(sys.executable,))},
            workspace_root=Path(r"\\wsl$\Ubuntu\home\vera\repo"),
            holder_prefix="test",
            delivery_lease_ttl=30,
            token=None,
        )
    assert not (tmp_path / "not-created.sqlite").exists()
