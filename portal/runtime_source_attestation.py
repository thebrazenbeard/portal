"""Content evidence for a *staged* desktop runtime, not a signature or authority grant."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .desktop_install import InstallSpec


SCHEMA = "PORTAL_RUNTIME_SOURCE_ATTESTATION_V1"
FILENAME = "RUNTIME_SOURCE_ATTESTATION.json"
_EPHEMERAL = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv",
})


def _is_ephemeral(relative: Path) -> bool:
    return any(
        part in _EPHEMERAL or part.endswith(".egg-info")
        for part in relative.parts
    )


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_reparse(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def _source_file_paths(root: Path):
    """Prune Git/cache trees before descent and reject redirected directories."""
    def onerror(exc: OSError) -> None:
        raise RuntimeError("staged source directory cannot be enumerated") from exc

    for parent_name, directories, filenames in os.walk(root, onerror=onerror):
        parent = Path(parent_name)
        retained = []
        for name in directories:
            candidate = parent / name
            if _is_reparse(candidate):
                raise RuntimeError("staged source contains a redirected directory")
            if not _is_ephemeral(candidate.relative_to(root)):
                retained.append(name)
        directories[:] = retained
        for name in sorted(filenames):
            path = parent / name
            if not _is_ephemeral(path.relative_to(root)):
                yield path


def _reject_executable_bytecode(root: Path) -> None:
    """A valid .pyc can execute different code from pristine .py source bytes."""
    for parent_name, directories, filenames in os.walk(root):
        parent = Path(parent_name)
        directories[:] = [name for name in directories if name != ".git"]
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                raise RuntimeError(
                    f"staged source has unverified executable bytecode: "
                    f"{(parent / name).relative_to(root)}"
                )


def _assert_checkout_identity(root: Path, expected_sha: str) -> None:
    """Compare actual bytes to pinned Git blobs, ignoring no index flags."""
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=True,
        )
        return completed.stdout

    if git("rev-parse", "HEAD").strip() != expected_sha:
        raise RuntimeError("staged source Git HEAD differs from install spec")
    # 'git diff' alone is insufficient: assume-unchanged and skip-worktree
    # flags can hide modified files. Read immutable HEAD blob contents.
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "HEAD"],
        capture_output=True, check=True,
    ).stdout
    for record in tracked.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise RuntimeError("staged source contains unsupported Git object")
        relative = Path(name.decode("utf-8", "surrogateescape"))
        path = root / relative
        if not path.is_file() or _is_reparse(path):
            raise RuntimeError("staged source tracked path is missing or redirected")
        pinned_bytes = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", oid.decode("ascii")],
            capture_output=True, check=True,
        ).stdout
        if hashlib.sha256(pinned_bytes).hexdigest() != _sha256_file(path)[0]:
            raise RuntimeError("staged source bytes differ from pinned Git blob")
    for name in git("ls-files", "--others", "--ignored", "--exclude-standard", "-z").split("\0"):
        if name and not _is_ephemeral(Path(name)):
            raise RuntimeError("staged source contains unexpected ignored files")
    for name in git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if name and not _is_ephemeral(Path(name)):
            raise RuntimeError("staged source contains unexpected untracked files")


def build_attestation(runtime_root: Path, spec: InstallSpec) -> dict[str, object]:
    """Hash all non-generated files of each exact staged source checkout."""
    spec.validate()
    runtime_root = Path(runtime_root)
    source_root = runtime_root / "sources"
    if _is_reparse(source_root):
        raise RuntimeError("runtime source directory cannot be a reparse point")
    sources: dict[str, object] = {}
    for source in spec.sources:
        root = source_root / source.component
        if not root.is_dir() or _is_reparse(root):
            raise RuntimeError(f"{source.component} source directory is missing or redirected")
        _reject_executable_bytecode(root)
        _assert_checkout_identity(root, source.sha)
        files: dict[str, object] = {}
        for path in _source_file_paths(root):
            relative = path.relative_to(root)
            if _is_reparse(path):
                raise RuntimeError(f"{source.component} source contains a reparse point")
            if path.is_file():
                digest, size = _sha256_file(path)
                files[relative.as_posix()] = {"sha256": digest, "size": size}
        if not files:
            raise RuntimeError(f"{source.component} source has no attestable files")
        sources[source.component] = {
            "repository": source.repository, "ref": source.ref,
            "sha": source.sha, "files": files,
        }
    body: dict[str, object] = {
        "schema": SCHEMA, "install_id": spec.install_id, "sources": sources,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**body, "sha256": hashlib.sha256(raw).hexdigest()}


def write_attestation(runtime_root: Path, spec: InstallSpec) -> dict[str, object]:
    """Capture the source bytes before starting a staged host."""
    payload = build_attestation(runtime_root, spec)
    path = Path(runtime_root) / FILENAME
    if path.exists() or path.is_symlink():
        raise RuntimeError("staged source attestation already exists")
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def verify_attestation(runtime_root: Path, spec: InstallSpec) -> dict[str, object]:
    """Detect additions, deletions, modifications and manifest divergence."""
    path = Path(runtime_root) / FILENAME
    if _is_reparse(path) or not path.is_file():
        raise RuntimeError("runtime source attestation is missing or redirected")
    try:
        expected = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise RuntimeError("runtime source attestation cannot be decoded") from exc
    observed = build_attestation(runtime_root, spec)
    if expected != observed:
        raise RuntimeError("staged runtime source attestation mismatch")
    return observed
