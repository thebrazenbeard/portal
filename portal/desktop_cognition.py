from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
import shutil
import subprocess
from typing import Callable, Mapping
import urllib.parse
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
    base_url: str | None = None
    api_key_env: str | None = None


CommandProbe = Callable[[str, tuple[str, ...]], str | None]
OllamaTagsProbe = Callable[[], Mapping[str, object] | None]
PreActiveModelsProbe = Callable[[], Mapping[str, object] | None]
PreActiveTargetProbe = Callable[[], Mapping[str, object] | None]
OpenAIModelsProbe = Callable[[str], Mapping[str, object] | None]


def _loopback_openai_base_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    base_url = value.strip().rstrip("/")
    if not base_url:
        return None
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.lower() == "localhost"
    return base_url if is_loopback else None


def _default_openai_models(
    base_url: str,
    *,
    api_key_env: str | None = None,
) -> Mapping[str, object] | None:
    headers = {"Accept": "application/json"}
    if api_key_env:
        token = os.environ.get(api_key_env)
        if not token:
            return None
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            value = json.load(response)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def discover_pre_active_target_routes(
    *,
    active_target_probe: PreActiveTargetProbe,
    models_probe: OpenAIModelsProbe | None = None,
) -> tuple[CognitionRoute, ...]:
    target = active_target_probe()
    if not isinstance(target, Mapping) or target.get("active") is not True:
        return ()
    if target.get("provider") != "openai-compatible":
        return ()

    name = target.get("name")
    model = target.get("model")
    base_url = _loopback_openai_base_url(target.get("base_url"))
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(model, str)
        or not model.strip()
        or base_url is None
    ):
        return ()

    api_key_env = target.get("api_key_env")
    if api_key_env is not None and not isinstance(api_key_env, str):
        return ()
    normalized_api_key_env = (
        api_key_env.strip() if isinstance(api_key_env, str) else None
    ) or None

    if models_probe is None:
        advertised = _default_openai_models(
            base_url,
            api_key_env=normalized_api_key_env,
        )
    else:
        advertised = models_probe(base_url)
    if not isinstance(advertised, Mapping):
        return ()
    data = advertised.get("data")
    if not isinstance(data, list):
        return ()
    model_id = model.strip()
    advertised_ids = {
        item.get("id")
        for item in data
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if model_id not in advertised_ids:
        return ()
    return (
        CognitionRoute(
            route_id=f"preactive-target:{name.strip()}",
            provider="pre_active_target",
            model_or_agent=model_id,
            local=True,
            available=True,
            current=True,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
            preference=1,
            base_url=base_url,
            api_key_env=normalized_api_key_env,
        ),
    )


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


def _default_pre_active_models() -> Mapping[str, object] | None:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:18081/v1/models",
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
    pre_active_models: PreActiveModelsProbe = _default_pre_active_models,
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

    pre_active = pre_active_models()
    if isinstance(pre_active, Mapping):
        models = pre_active.get("data")
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, Mapping):
                    continue
                raw_id = item.get("id")
                if not isinstance(raw_id, str) or not raw_id.strip():
                    continue
                model_id = raw_id.strip()
                adapter_active = item.get("adapter_active") is True
                effect_authority = item.get("effect_authority")
                if effect_authority is not False:
                    continue
                adapter_sha = item.get("adapter_model_sha256")
                base_revision = item.get("base_model_revision")
                valid_adapter_sha = (
                    isinstance(adapter_sha, str)
                    and len(adapter_sha) == 64
                    and all(ch in "0123456789abcdef" for ch in adapter_sha)
                )
                valid_base_revision = (
                    isinstance(base_revision, str)
                    and len(base_revision) == 40
                    and all(ch in "0123456789abcdef" for ch in base_revision)
                )
                trained_vera = (
                    adapter_active
                    and model_id.lower().startswith("vera")
                    and valid_adapter_sha
                    and valid_base_revision
                )
                observed_version = (
                    adapter_sha
                    if valid_adapter_sha
                    else base_revision
                    if valid_base_revision
                    else None
                )
                routes.append(
                    CognitionRoute(
                        route_id=f"preactive:{model_id}",
                        provider="pre_active_local",
                        model_or_agent=model_id,
                        local=True,
                        available=True,
                        current=True,
                        capabilities=("text",),
                        incremental_paid_compute=False,
                        auto_admissible=True,
                        effect_authority_ceiling=(
                            "COGNITION_ONLY_NO_PROTECTED_EFFECT"
                        ),
                        preference=5 if trained_vera else 30,
                        observed_version=(
                            str(observed_version)
                            if observed_version is not None
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
