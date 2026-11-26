from types import SimpleNamespace
from pathlib import Path
import pytest
from portal._runner import cli

def test_packaged_resources_do_not_narrow_checkout_exclusion(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    resources = checkout / "portal" / "_data"
    resources.mkdir(parents=True)
    registry = checkout / "private-fixture.yaml"
    registry.write_text("fixture: true")
    monkeypatch.setattr(cli, "ROOT", resources)
    monkeypatch.setattr(cli, "PUBLIC_CHECKOUT_ROOT", checkout)
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", str(registry))
    with pytest.raises(ValueError, match="outside"):
        cli._project_registry_path()
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", str(tmp_path / "external.yaml"))
    monkeypatch.setattr(cli, "_load_worker_registry_snapshot", lambda: pytest.fail("must refuse before claim"))
    with pytest.raises(ValueError, match="outside"):
        cli._claim_worker_route(SimpleNamespace(payload_out=checkout/"fixture.json"))
    assert not (checkout/"fixture.json").exists()
    monkeypatch.delenv("PROJECT_RUNNER_PROJECT_REGISTRY")
    assert cli._project_registry_path() == resources/"registry"/"projects.yaml"
