from pathlib import Path
from zipfile import ZipFile

from scripts.check_disjoint_wheels import assess_wheels


def _wheel(tmp_path: Path, role: str, *, extra=(), command=None) -> Path:
    distributions = {
        "portal": ("portal-coordinator", ("portal/__init__.py", "portal/_runner/__init__.py"), "portal = portal.cli:entrypoint\nportal-desktop = portal.desktop_app:main"),
        "runner": ("project-runner", ("runner/__init__.py",), "project-runner = runner.cli:entrypoint"),
        "mono": ("vera-mono", ("vera_core/__init__.py",), "vera-mono = vera_core.cli:main"),
    }
    name, files, scripts = distributions[role]
    path = tmp_path / f"{role}.whl"
    with ZipFile(path, "w") as archive:
        for filename in (*files, *extra):
            archive.writestr(filename, "")
        archive.writestr(f"{role}-1.0.dist-info/METADATA", f"Name: {name}\nVersion: 1.0\n")
        archive.writestr(f"{role}-1.0.dist-info/entry_points.txt", f"[console_scripts]\n{command or scripts}\n")
    return path


def _trio(tmp_path: Path, **overrides):
    return {role: overrides.get(role) or _wheel(tmp_path, role) for role in ("portal", "runner", "mono")}


def test_disjoint_ownership_is_admitted(tmp_path):
    result = assess_wheels(_trio(tmp_path))
    assert result["qualified_source_ownership"] is True
    assert result["archive_collisions"] == {}


def test_collision_rejected_even_when_bytes_could_match(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert result["archive_collisions"] == {"portal/runner": ["runner/__init__.py"]}


def test_cli_name_double_ownership_is_rejected(tmp_path):
    bad = _wheel(tmp_path, "mono", command="portal = vera_core.cli:main")
    result = assess_wheels(_trio(tmp_path, mono=bad))
    assert result["qualified_source_ownership"] is False
    assert any("claimed by multiple" in issue for issue in result["problems"])


def test_private_runner_namespace_is_required(tmp_path):
    path = _wheel(tmp_path, "portal")
    with ZipFile(path, "a") as archive:
        archive.writestr("portal/_runner/ignored.txt", "")
    # Synthetic omission rather than installing or altering a real package.
    missing = tmp_path / "missing.whl"
    with ZipFile(path) as source, ZipFile(missing, "w") as dest:
        for member in source.namelist():
            if member != "portal/_runner/__init__.py":
                dest.writestr(member, source.read(member))
    result = assess_wheels(_trio(tmp_path, portal=missing))
    assert result["qualified_source_ownership"] is False
    assert any("missing owned path portal/_runner/__init__.py" in issue for issue in result["problems"])
