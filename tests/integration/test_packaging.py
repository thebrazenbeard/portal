from pathlib import Path
import ast
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_setuptools_explicitly_packages_portal_private_runner_without_public_collision():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "portal-coordinator"
    assert config["tool"]["setuptools"]["packages"] == ["portal", "portal._runner"]
    assert "project-runner" not in config["project"]["scripts"]


def test_portal_source_does_not_import_the_standalone_runner_namespace():
    for path in (ROOT / "portal").rglob("*.py"):
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "runner" and not node.module.startswith("runner."), path
            if isinstance(node, ast.Import):
                assert all(alias.name != "runner" and not alias.name.startswith("runner.") for alias in node.names), path
