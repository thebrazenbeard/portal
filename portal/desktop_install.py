from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Callable


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_COMPONENTS = ("vera-mono", "portal", "pre-active", "volition")


class StageRootConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSource:
    component: str
    repository: str
    ref: str
    sha: str

    def validate(self) -> None:
        if self.component not in _COMPONENTS:
            raise ValueError(f"unsupported runtime component: {self.component}")
        if "/" not in self.repository or self.repository.count("/") != 1:
            raise ValueError(f"invalid repository: {self.repository}")
        if not self.ref.strip():
            raise ValueError("source ref is required")
        if not _SHA_RE.fullmatch(self.sha):
            raise ValueError(f"source sha must be exact 40-char lowercase hex: {self.component}")

    def to_mapping(self) -> dict[str, str]:
        self.validate()
        return {
            "component": self.component,
            "repository": self.repository,
            "ref": self.ref,
            "sha": self.sha,
        }


@dataclass(frozen=True)
class InstallSpec:
    schema: str
    install_id: str
    sources: tuple[RuntimeSource, ...]

    def validate(self) -> None:
        if self.schema != "VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1":
            raise ValueError("unsupported install spec schema")
        if not self.install_id.strip():
            raise ValueError("install_id is required")
        if len(self.sources) != 4:
            raise ValueError("install spec requires exactly four sources")
        names = [source.component for source in self.sources]
        if len(set(names)) != len(names):
            raise ValueError("duplicate runtime component")
        if tuple(names) != _COMPONENTS:
            raise ValueError(
                "runtime sources must be ordered vera-mono, portal, pre-active, volition"
            )
        for source in self.sources:
            source.validate()

    def source_map(self) -> dict[str, RuntimeSource]:
        self.validate()
        return {source.component: source for source in self.sources}

    def to_mapping(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": self.schema,
            "install_id": self.install_id,
            "sources": [source.to_mapping() for source in self.sources],
            "activation": {
                "requested": False,
                "qualified": False,
                "active": False,
            },
            "claim_ceiling": (
                "FROZEN_SOURCE_INSTALL_SPEC_ONLY_NOT_INSTALLED_NOT_QUALIFIED_NOT_ACTIVE"
            ),
        }

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                self.to_mapping(),
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> "InstallSpec":
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("install spec must be an object")
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise ValueError("install spec sources must be a list")
        sources = tuple(
            RuntimeSource(
                component=str(item["component"]),
                repository=str(item["repository"]),
                ref=str(item["ref"]),
                sha=str(item["sha"]),
            )
            for item in raw_sources
            if isinstance(item, dict)
        )
        spec = cls(
            schema=str(payload.get("schema", "")),
            install_id=str(payload.get("install_id", "")),
            sources=sources,
        )
        spec.validate()
        return spec


RemoteResolver = Callable[[str, str], str]


def resolve_remote_head(repository: str, ref: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "ls-remote",
            f"https://github.com/{repository}.git",
            f"refs/heads/{ref}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git ls-remote failed for {repository}@{ref}: "
            f"{completed.stderr.strip()}"
        )
    line = completed.stdout.strip().splitlines()
    if len(line) != 1:
        raise RuntimeError(f"remote ref did not resolve uniquely: {repository}@{ref}")
    sha = line[0].split()[0]
    if not _SHA_RE.fullmatch(sha):
        raise RuntimeError(f"remote ref did not resolve to an exact sha: {repository}@{ref}")
    return sha


def freeze_install_spec(
    *,
    install_id: str,
    portal_ref: str,
    portal_sha: str,
    resolve_remote: RemoteResolver = resolve_remote_head,
) -> InstallSpec:
    sources = (
        RuntimeSource(
            "vera-mono",
            "thebrazenbeard/vera-mono",
            "main",
            resolve_remote("thebrazenbeard/vera-mono", "main"),
        ),
        RuntimeSource(
            "portal",
            "thebrazenbeard/portal",
            portal_ref,
            portal_sha,
        ),
        RuntimeSource(
            "pre-active",
            "thebrazenbeard/pre-active",
            "main",
            resolve_remote("thebrazenbeard/pre-active", "main"),
        ),
        RuntimeSource(
            "volition",
            "thebrazenbeard/volition",
            "main",
            resolve_remote("thebrazenbeard/volition", "main"),
        ),
    )
    spec = InstallSpec(
        schema="VERA_DESKTOP_RUNTIME_INSTALL_SPEC_V1",
        install_id=install_id,
        sources=sources,
    )
    spec.validate()
    return spec


def prepare_stage_root(root: Path, spec: InstallSpec) -> Path:
    root = Path(root)
    spec.validate()
    existing = root / "RUNTIME_INSTALL_SPEC.json"
    if existing.exists():
        prior = InstallSpec.read(existing)
        if prior != spec:
            raise StageRootConflict(
                "stage root is already bound to a different exact source spec"
            )
    root.mkdir(parents=True, exist_ok=True)
    for relative in (
        "sources",
        "state",
        "bridge/requests",
        "bridge/responses",
        "host",
        "logs",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    if not existing.exists():
        spec.write(existing)
    return root
