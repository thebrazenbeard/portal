from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
from typing import Callable, Mapping
import urllib.request


@dataclass(frozen=True)
class CognitionRequest:
    required_capabilities: tuple[str, ...] = ("text",)


@dataclass(frozen=True)
class CognitionRoute:
    route_id: str
    provider: str
    model_or_agent: str
    local: bool
    available: bool
    current: bool
    capabilities: tuple[str, ...]
    incremental_paid_compute: bool | None
    auto_admissible: bool
    effect_authority_ceiling: str
    preference: int = 100
    observed_version: str | None = None


CommandProbe = Callable[[str, tuple[str, ...]], str | None]
OllamaTagsProbe = Callable[[], Mapping[str, object] | None]


def _default_command_probe(name: str, args: tuple[str, ...]) -> str | None:
    executable = shutil.which(name)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value or name


def _default_ollama_tags() -> Mapping[str, object] | None:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags",
            timeout=2.0,
        ) as response:
            value = json.load(response)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def discover_cognition_routes(
    *,
    command_probe: CommandProbe = _default_command_probe,
    ollama_tags: OllamaTagsProbe = _default_ollama_tags,
) -> tuple[CognitionRoute, ...]:
    routes: list[CognitionRoute] = []

    tags = ollama_tags()
    if isinstance(tags, Mapping):
        models = tags.get("models")
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, Mapping):
                    continue
                raw_name = item.get("name")
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                name = raw_name.strip()
                preference = 10 if name == "vera-local:latest" else 20
                routes.append(
                    CognitionRoute(
                        route_id=f"ollama:{name}",
                        provider="ollama",
                        model_or_agent=name,
                        local=True,
                        available=True,
                        current=True,
                        capabilities=("text",),
                        incremental_paid_compute=False,
                        auto_admissible=True,
                        effect_authority_ceiling=(
                            "COGNITION_ONLY_NO_PROTECTED_EFFECT"
                        ),
                        preference=preference,
                        observed_version=(
                            str(item["modified_at"])
                            if item.get("modified_at")
                            else None
                        ),
                    )
                )

    codex_version = command_probe("codex", ("--version",))
    if codex_version is not None:
        routes.append(
            CognitionRoute(
                route_id="codex:cli",
                provider="codex",
                model_or_agent="codex-cli",
                local=False,
                available=True,
                current=True,
                capabilities=("text",),
                incremental_paid_compute=None,
                auto_admissible=False,
                effect_authority_ceiling=(
                    "COGNITION_ONLY_NO_PROTECTED_EFFECT"
                ),
                preference=50,
                observed_version=codex_version,
            )
        )

    return tuple(sorted(routes, key=lambda route: route.route_id))


def select_cognition_route(
    request: CognitionRequest,
    routes: tuple[CognitionRoute, ...],
    *,
    authorized_route_ids: frozenset[str] = frozenset(),
) -> CognitionRoute | None:
    required = frozenset(request.required_capabilities)
    candidates: list[CognitionRoute] = []
    for route in routes:
        if not route.available or not route.current:
            continue
        if not required.issubset(route.capabilities):
            continue
        if not route.auto_admissible and route.route_id not in authorized_route_ids:
            continue
        candidates.append(route)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda route: (
            0 if route.local else 1,
            0 if route.incremental_paid_compute is False else 1,
            route.preference,
            route.route_id,
        ),
    )
