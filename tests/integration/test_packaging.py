from pathlib import Path
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_setuptools_explicitly_packages_only_runner():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["packages"] == ["runner"]


def _run(arguments, cwd):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for name in tuple(env):
        if name.startswith("PROJECT_RUNNER_"):
            env.pop(name)
    result = subprocess.run(
        [sys.executable, *arguments], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("packaging")
    source = workspace / "source"
    source.mkdir()
    for name in ("runner", "registry", "schemas", "policy", "topology", "portfolio"):
        shutil.copytree(
            ROOT / name, source / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE", "NOTICE"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, source / name)
    # Untracked local/private files must never become distribution resources.
    (source / "registry" / "private.yaml").write_text("private sentinel", encoding="utf-8")
    dist = workspace / "dist"
    dist.mkdir()
    _run(["-c", "from setuptools.build_meta import build_sdist; import sys; build_sdist(sys.argv[1])", str(dist)], source)
    sdist = next(dist.glob("*.tar.gz"))
    unpacked = workspace / "unpacked"
    with tarfile.open(sdist) as archive:
        assert not any(name.endswith("registry/private.yaml") for name in archive.getnames())
        archive.extractall(unpacked, filter="data")
    rebuilt_source = next(unpacked.iterdir())
    stale = rebuilt_source / "build" / "lib" / "runner" / "data" / "private.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale private sentinel", encoding="utf-8")
    _run(["-c", "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])", str(dist)], rebuilt_source)
    wheel = next(dist.glob("*.whl"))
    target = workspace / "installed"
    _run(["-m", "pip", "install", "--no-deps", "--no-index", "--target", str(target), str(wheel)], workspace)
    elsewhere = workspace / "unrelated cwd"
    elsewhere.mkdir()
    return target, elsewhere, wheel


def test_distributions_include_only_required_public_runtime_data(installed_package):
    _, _, wheel = installed_package
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    expected = {
        "registry/projects.yaml", "registry/workers.yaml",
        "policy/scheduling.yaml", "topology/dependencies.yaml",
        "portfolio/corpus.public.json", "portfolio/advancement_wave.public.json",
        *(f"schemas/{name}.schema.json" for name in (
            "project", "worker", "observation", "dependency", "frontier",
            "work-unit", "portfolio-corpus", "portfolio-advancement", "p0-project-manifest",
        )),
    }
    assert {"runner/data/" + name for name in expected} <= names
    assert not any(name.endswith("private.yaml") for name in names)


@pytest.mark.parametrize("command", [
    "validate", "inventory", "portfolio-wave-plan", "portfolio-operator-bindings",
])
def test_installed_cli_runs_without_the_checkout(installed_package, command):
    target, elsewhere, _ = installed_package
    script = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(target)!r}); "
        "import runner; "
        f"assert Path(runner.__file__).is_relative_to({str(target)!r}); "
        "from runner.cli import main; raise SystemExit(main(sys.argv[1:]))"
    )
    arguments = ["-c", script, command]
    if command == "portfolio-wave-plan":
        arguments.extend([
            "--max-parallel", "2", "--max-per-identity", "1", "--max-per-family", "1",
        ])
    output = _run(arguments, elsewhere)
    assert output.strip()
    if command == "validate":
        assert "registries valid" in output.lower()
    if command == "inventory":
        assert "projects: 15" in output.lower()
        assert "workers: 13" in output.lower()


def test_installed_default_policy_and_schemas_ignore_current_directory(installed_package):
    target, elsewhere, _ = installed_package
    decoy = elsewhere / "registry"
    decoy.mkdir(exist_ok=True)
    (decoy / "projects.yaml").write_text("invalid decoy", encoding="utf-8")
    script = (
        "import sys; "
        f"sys.path.insert(0, {str(target)!r}); "
        "from runner.cli import main; "
        "from runner.prioritize import rank_frontiers; "
        "from runner.schema import validate_document; "
        "assert rank_frontiers([]) == (); "
        "validate_document('project', {'projects': []}); "
        "raise SystemExit(main(['validate']))"
    )
    assert "registries valid" in _run(["-c", script], elsewhere).lower()
