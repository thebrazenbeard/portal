"""Content evidence for a *staged* desktop runtime, not a signature or authority grant."""

from __future__ import annotations

import hashlib
import json
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


def _assert_checkout_identity(root: Path, expected_sha: str) -> None:
    """Prove staged working bytes still belong to the pinned detached Git tree."""
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=True,
        )
        return completed.stdout

    if git("rev-parse", "HEAD").strip() != expected_sha:
        raise RuntimeError("staged source Git HEAD differs from install spec")
    if git("diff", "--name-only", "HEAD", "--").strip():
        raise RuntimeError("staged source modified relative to Git HEAD")
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
        _assert_checkout_identity(root, source.sha)
        files: dict[str, object] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if _is_ephemeral(relative):
                continue
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
