import base64
import hashlib
import warnings
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
    stem = name.replace("-", "_") + "-1.0"
    path = tmp_path / f"{role}.whl"
    members = {filename: b"" for filename in (*files, *extra)}
    members[f"{stem}.dist-info/METADATA"] = f"Name: {name}\nVersion: 1.0\n".encode()
    members[f"{stem}.dist-info/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    members[f"{stem}.dist-info/entry_points.txt"] = f"[console_scripts]\n{command or scripts}\n".encode()
    rows = []
    for filename, content in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        rows.append(f"{filename},sha256={digest},{len(content)}")
    record = f"{stem}.dist-info/RECORD"
    members[record] = ("\n".join([*rows, f"{record},,"]) + "\n").encode()
    with ZipFile(path, "w") as archive:
        for filename, content in members.items():
            archive.writestr(filename, content)
    return path


def _trio(tmp_path: Path, **overrides):
    return {role: overrides.get(role) or _wheel(tmp_path, role) for role in ("portal", "runner", "mono")}


def test_disjoint_ownership_is_admitted(tmp_path):
    result = assess_wheels(_trio(tmp_path))
    assert result["qualified_source_ownership"] is True
    assert result["installed_destination_collisions"] == {}


def test_collision_rejected_even_when_bytes_could_match(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert result["installed_destination_collisions"] == {"portal/runner": ["site-packages/runner/__init__.py"]}


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


def test_purelib_relocation_into_runner_is_rejected(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("portal_coordinator-1.0.data/purelib/runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert result["installed_destination_collisions"]["portal/runner"] == ["site-packages/runner/__init__.py"]


def test_platlib_relocation_into_runner_is_rejected(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("portal_coordinator-1.0.data/platlib/runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert result["installed_destination_collisions"]["portal/runner"] == ["site-packages/runner/__init__.py"]


def test_windows_case_alias_is_rejected(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("Runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert result["installed_destination_collisions"]["portal/runner"] == ["site-packages/runner/__init__.py"]


def test_duplicate_archive_member_is_rejected(tmp_path):
    bad = _wheel(tmp_path, "portal")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(bad, "a") as archive:
            archive.writestr("portal/__init__.py", b"collision")
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert any("duplicate" in issue for issue in result["problems"])


def test_wheel_script_and_generated_console_script_collide(tmp_path):
    bad = _wheel(tmp_path, "mono", extra=("vera_mono-1.0.data/scripts/portal",))
    result = assess_wheels(_trio(tmp_path, mono=bad))
    assert result["qualified_source_ownership"] is False
    assert result["installed_destination_collisions"]["portal/mono"] == ["scripts/portal"]


def test_windows_exe_script_alias_overwrites_generated_portal_launcher(tmp_path):
    # RECORD-valid wheel member matches the actual Windows CLI wrapper target.
    bad = _wheel(tmp_path, "portal", extra=("portal_coordinator-1.0.data/scripts/portal.exe",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert any("scripts/portal.exe" in issue for issue in result["problems"])


def test_windows_exe_script_alias_collides_across_wheels(tmp_path):
    bad = _wheel(tmp_path, "mono", extra=("vera_mono-1.0.data/scripts/portal.exe",))
    result = assess_wheels(_trio(tmp_path, mono=bad))
    assert result["qualified_source_ownership"] is False
    assert "scripts/portal.exe" in result["installed_destination_collisions"]["portal/mono"]


def test_unsafe_archive_path_rejected(tmp_path):
    bad = _wheel(tmp_path, "portal", extra=("portal_coordinator-1.0.data/purelib/../runner/__init__.py",))
    result = assess_wheels(_trio(tmp_path, portal=bad))
    assert result["qualified_source_ownership"] is False
    assert any("unsafe" in issue for issue in result["problems"])
