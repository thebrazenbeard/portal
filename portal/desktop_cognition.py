from __future__ import annotations

from dataclasses import dataclass, replace
import ipaddress
import json
import math
import os
import shutil
import subprocess
import time
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
    observed_at: float | None = None
    expires_at: float | None = None


def route_is_current(route: CognitionRoute, *, now: float | None = None) -> bool:
    if not route.current:
        return False
    if route.observed_at is None and route.expires_at is None:
        return True  # Compatibility for explicitly supplied test/embedded routes.
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
               for value in (route.observed_at, route.expires_at)):
        return False
    clock = time.time() if now is None else now
    return route.observed_at <= clock < route.expires_at


def loopback_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("cognition URL must use a loopback endpoint")
    parsed = urllib.parse.urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("cognition URL must use an http(s) loopback endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("cognition URL must not include credentials, query or fragment")
    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        hostname = "127.0.0.1"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise ValueError("cognition URL must use a loopback endpoint") from exc
    if not address.is_loopback:
        raise ValueError("cognition URL must use a loopback endpoint")
    host = f"[{address}]" if address.version == 6 else str(address)
    port = parsed.port  # Invalid/out-of-range ports fail before transport.
    authority = f"{host}:{port}" if port is not None else host
    return urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("local cognition redirects are prohibited")


def loopback_json(request: urllib.request.Request, *, timeout: float) -> dict[str, object]:
    """Local-only transport: environment proxies and HTTP redirects are disabled."""
    safe_url = loopback_base_url(request.full_url)
    safe_request = urllib.request.Request(
        safe_url, data=request.data, headers=dict(request.header_items()),
        method=request.get_method(),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(safe_request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("local cognition response must be an object")
    return value


def ollama_model_is_remote(item: Mapping[str, object], name: str) -> bool:
    details = item.get("details")
    metadata = details if isinstance(details, Mapping) else {}
    return (
        bool(item.get("remote_host") or item.get("remote_model")
             or metadata.get("remote_host") or metadata.get("remote_model"))
        or name.lower().endswith((":cloud", "-cloud"))
    )


def _deduplicate(routes: list[CognitionRoute]) -> tuple[CognitionRoute, ...]:
    grouped: dict[str, list[CognitionRoute]] = {}
    for route in routes:
        grouped.setdefault(route.route_id, []).append(route)
    result = []
    for route_id in sorted(grouped):
        entries = grouped[route_id]
        route = min(entries, key=lambda entry: (entry.observed_version or "", entry.local))
        if any(entry != route for entry in entries):
            route = replace(route, current=False, auto_admissible=False,
                            local=all(entry.local for entry in entries),
                            incremental_paid_compute=None)
        result.append(route)
    return tuple(result)


CommandProbe = Callable[[str, tuple[str, ...]], str | None]
OllamaTagsProbe = Callable[[], Mapping[str, object] | None]
PreActiveModelsProbe = Callable[[], Mapping[str, object] | None]
PreActiveTargetProbe = Callable[[], Mapping[str, object] | None]
OpenAIModelsProbe = Callable[[str], Mapping[str, object] | None]


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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value or name


def _default_ollama_tags() -> Mapping[str, object] | None:
    try:
        value = loopback_json(urllib.request.Request("http://127.0.0.1:11434/api/tags"), timeout=2.0)
    except (OSError, ValueError, RuntimeError):
        return None
    return value if isinstance(value, dict) else None


def _default_pre_active_models() -> Mapping[str, object] | None:
    try:
        value = loopback_json(urllib.request.Request("http://127.0.0.1:18081/v1/models"), timeout=2.0)
    except (OSError, ValueError, RuntimeError):
        return None
    return value if isinstance(value, dict) else None


def discover_cognition_routes(
    *,
    command_probe: CommandProbe = _default_command_probe,
    ollama_tags: OllamaTagsProbe = _default_ollama_tags,
    pre_active_models: PreActiveModelsProbe = _default_pre_active_models,
    allow_local_no_paid_compute: bool = False,
    now: float | None = None,
    ttl_seconds: float = 30.0,
) -> tuple[CognitionRoute, ...]:
    if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
        raise ValueError("route ttl_seconds must be finite and positive")
    observed = time.time() if now is None else now
    routes: list[CognitionRoute] = []
    ollama_version = command_probe("ollama", ("--version",))
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
                remote = ollama_model_is_remote(item, name)
                preference = 10 if name == "vera-local:latest" else 20
                routes.append(
                    CognitionRoute(
                        route_id=f"ollama:{name}",
                        provider="ollama",
                        model_or_agent=name,
                        local=not remote,
                        available=True,
                        current=True,
                        capabilities=("text",),
                        incremental_paid_compute=None if remote else False,
                        auto_admissible=allow_local_no_paid_compute is True and not remote,
                        effect_authority_ceiling=(
                            "COGNITION_ONLY_NO_PROTECTED_EFFECT"
                        ),
                        preference=preference,
                        observed_version=(
                            str(item.get("digest") or item["modified_at"])
                            if item.get("digest") or item.get("modified_at")
                            else None
                        ),
                        base_url="http://127.0.0.1:11434",
                        observed_at=observed, expires_at=observed + ttl_seconds,
                    )
                )

    if not routes and ollama_version is not None:
        routes.append(CognitionRoute(
            route_id="ollama:cli", provider="ollama", model_or_agent="ollama-cli",
            local=True, available=False, current=True, capabilities=(),
            incremental_paid_compute=None, auto_admissible=False,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
            observed_version=ollama_version, observed_at=observed,
            expires_at=observed + ttl_seconds,
        ))

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
                        auto_admissible=allow_local_no_paid_compute is True,
                        effect_authority_ceiling=(
                            "COGNITION_ONLY_NO_PROTECTED_EFFECT"
                        ),
                        base_url="http://127.0.0.1:18081/v1",
                        observed_at=observed, expires_at=observed + ttl_seconds,
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
                observed_at=observed, expires_at=observed + ttl_seconds,
            )
        )

    return _deduplicate(routes)


def discover_pre_active_target_routes(
    *, active_target_probe: PreActiveTargetProbe,
    models_probe: OpenAIModelsProbe | None = None,
    allow_local_no_paid_compute: bool = False,
    now: float | None = None,
    ttl_seconds: float = 30.0,
) -> tuple[CognitionRoute, ...]:
    target = active_target_probe()
    if not isinstance(target, Mapping) or target.get("active") is not True or target.get("provider") != "openai-compatible":
        return ()
    name, model = target.get("name"), target.get("model")
    if not all(isinstance(value, str) and value.strip() for value in (name, model)):
        return ()
    try:
        base_url = loopback_base_url(target.get("base_url"))
    except ValueError:
        return ()
    api_key_env = target.get("api_key_env")
    if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env.strip()):
        return ()
    api_key_env = api_key_env.strip() if isinstance(api_key_env, str) else None
    if models_probe is None:
        headers = {"Accept": "application/json"}
        if api_key_env:
            token = os.environ.get(api_key_env)
            if not token:
                return ()
            headers["Authorization"] = f"Bearer {token}"
        try:
            advertised = loopback_json(urllib.request.Request(f"{base_url}/models", headers=headers), timeout=2.0)
        except (ValueError, OSError, RuntimeError):
            return ()
    else:
        advertised = models_probe(base_url)
    if not isinstance(advertised, Mapping) or not isinstance(advertised.get("data"), list):
        return ()
    entries = [item for item in advertised["data"] if isinstance(item, Mapping) and item.get("id") == model.strip()]
    if len(entries) != 1:
        return ()
    if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
        raise ValueError("route ttl_seconds must be finite and positive")
    observed = time.time() if now is None else now
    entry = entries[0]
    return (CognitionRoute(
        route_id=f"preactive-target:{name.strip()}", provider="pre_active_target",
        model_or_agent=model.strip(), local=True, available=True, current=True,
        capabilities=("text",), incremental_paid_compute=False,
        auto_admissible=allow_local_no_paid_compute is True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT", preference=1,
        base_url=base_url, api_key_env=api_key_env,
        observed_version=str(entry.get("adapter_model_sha256") or entry.get("base_model_revision") or "") or None,
        observed_at=observed, expires_at=observed + ttl_seconds,
    ),)


def select_cognition_route(
    request: CognitionRequest,
    routes: tuple[CognitionRoute, ...],
    *,
    authorized_route_ids: frozenset[str] = frozenset(),
    authorized_paid_route_ids: frozenset[str] = frozenset(),
    now: float | None = None,
) -> CognitionRoute | None:
    required = frozenset(request.required_capabilities)
    candidates: list[CognitionRoute] = []
    for route in routes:
        if not route.available or not route_is_current(route, now=now):
            continue
        if not required.issubset(route.capabilities):
            continue
        if route.effect_authority_ceiling != "COGNITION_ONLY_NO_PROTECTED_EFFECT":
            continue
        local_policy_admits = route.auto_admissible and route.local and route.incremental_paid_compute is False
        if not local_policy_admits and route.route_id not in authorized_route_ids:
            continue
        if route.incremental_paid_compute is not False and route.route_id not in authorized_paid_route_ids:
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
