from __future__ import annotations

import json

import pytest

from portal import desktop_cognition
from portal.desktop_cognition import (
    CognitionRequest,
    CognitionRoute,
    discover_cognition_routes,
    select_cognition_route,
)

OLLAMA_DIGEST = "a" * 64


def _stub_discovery(monkeypatch, body, status=200):
    encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    seen = {"connections": [], "requests": []}

    class Response:
        def __init__(self):
            self.status = status

        def read(self, limit):
            seen["read_limit"] = limit
            return encoded[:limit]

        def close(self):
            seen["response_closed"] = True

    class Connection:
        def __init__(self, host, port, timeout):
            seen["connections"].append((host, port, timeout))

        def request(self, method, path, headers):
            seen["requests"].append((method, path, headers))

        def getresponse(self):
            return Response()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(desktop_cognition, "_DeadlineHTTPConnection", Connection)
    return seen


@pytest.mark.parametrize("provider,port,path,method", [
    ("ollama", 11434, "/api/tags", desktop_cognition._default_ollama_tags),
    ("pre_active", 18081, "/v1/models", desktop_cognition._default_pre_active_models),
])
def test_local_discovery_is_direct_loopback_and_proxy_independent(
    monkeypatch, provider, port, path, method,
):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example:8080")
    payload = {"models": [], "data": []}
    seen = _stub_discovery(monkeypatch, payload)
    assert method() == payload
    assert seen["connections"] == [("127.0.0.1", port, 2.0)]
    assert seen["requests"] == [("GET", path, {"Accept": "application/json"})]
    assert seen["closed"] is True
    assert seen["response_closed"] is True


@pytest.mark.parametrize("status", [302, 307, 401, 503])
def test_local_discovery_does_not_follow_redirects(monkeypatch, status):
    seen = _stub_discovery(monkeypatch, {}, status=status)
    assert desktop_cognition._default_ollama_tags() is None
    assert len(seen["connections"]) == 1


@pytest.mark.parametrize("body", [
    pytest.param(b"\xff", id="invalid-utf8"),
    pytest.param(
        b"x" * (desktop_cognition._MAX_LOCAL_DISCOVERY_BYTES + 1),
        id="oversized-response",
    ),
    pytest.param(b"[]", id="unexpected-json-array"),
])
def test_local_discovery_rejects_invalid_or_oversized_body(monkeypatch, body):
    seen = _stub_discovery(monkeypatch, body)
    assert desktop_cognition._default_pre_active_models() is None
    assert seen["read_limit"] == desktop_cognition._MAX_LOCAL_DISCOVERY_BYTES + 1


def test_local_discovery_rejects_duplicate_registry_json_keys(monkeypatch):
    raw = (b'{"models":[{"name":"vera-local:latest","digest":"' +
           OLLAMA_DIGEST.encode("ascii") + b'","digest":"' +
           ("b" * 64).encode("ascii") + b'"}]}')
    _stub_discovery(monkeypatch, raw)
    assert desktop_cognition._default_ollama_tags() is None


def test_ollama_models_are_discovered_as_local_no_incremental_paid_text_routes() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda name, args: (
            "codex-cli 1.2.3" if name == "codex" else None
        ),
        ollama_tags=lambda: {
            "models": [
                {"name": "vera-local:latest", "digest": OLLAMA_DIGEST,
                 "modified_at": "2026-10-06T20:00:00Z"},
                {"name": "qwen3:8b", "digest": "b" * 64,
                 "modified_at": "2026-10-01T12:00:00Z"},
            ]
        },
    )
    by_id = {route.route_id: route for route in routes}

    local = by_id["ollama:vera-local:latest"]
    assert local.provider == "ollama"
    assert local.local is True
    assert local.incremental_paid_compute is False
    assert local.auto_admissible is True
    assert local.capabilities == ("text",)
    assert local.effect_authority_ceiling == "COGNITION_ONLY_NO_PROTECTED_EFFECT"
    assert local.observed_registry_digest == OLLAMA_DIGEST

    codex = by_id["codex:cli"]
    assert codex.available is True
    assert codex.incremental_paid_compute is None
    assert codex.auto_admissible is False


def test_trained_pre_active_adapter_is_discovered_and_preferred_over_ollama_vera() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: {
            "models": [
                {"name": "vera-local:latest", "digest": OLLAMA_DIGEST,
                 "modified_at": "2026-08-01T00:00:00Z"},
            ]
        },
        pre_active_models=lambda: {
            "object": "list",
            "data": [
                {
                    "id": "vera-v10r3-step20",
                    "object": "model",
                    "adapter_active": True,
                    "adapter_model_sha256": "b2d6eec7" + "0" * 56,
                    "base_model_revision": "d61dd146c8fd44c9a49cdb7f59f34e17b61902d8",
                    "effect_authority": False,
                }
            ],
        },
    )
    by_id = {route.route_id: route for route in routes}
    trained = by_id["preactive:vera-v10r3-step20"]
    assert trained.provider == "pre_active_local"
    assert trained.local is True
    assert trained.incremental_paid_compute is False
    assert trained.auto_admissible is True
    assert trained.preference == 5
    assert trained.observed_version == "b2d6eec7" + "0" * 56

    selected = select_cognition_route(CognitionRequest(), routes)
    assert selected is not None
    assert selected.route_id == "preactive:vera-v10r3-step20"


def test_base_only_pre_active_model_is_discovered_but_ollama_vera_remains_preferred() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: {
            "models": [
                {"name": "vera-local:latest", "digest": OLLAMA_DIGEST,
                 "modified_at": "2026-08-01T00:00:00Z"},
            ]
        },
        pre_active_models=lambda: {
            "object": "list",
            "data": [
                {
                    "id": "qwen3.5-4b-local",
                    "object": "model",
                    "adapter_active": False,
                    "effect_authority": False,
                }
            ],
        },
    )

    selected = select_cognition_route(CognitionRequest(), routes)
    assert selected is not None
    assert selected.route_id == "ollama:vera-local:latest"


@pytest.mark.parametrize("models", [
    [{"name": "vera-local:latest"}],
    [{"name": "vera-local:latest", "digest": "A" * 64}],
    [{"name": "vera-local:latest", "digest": OLLAMA_DIGEST}] * 2,
    [{"name": "vera-local:latest", "digest": "b" * 64},
     {"name": "vera-local:latest", "digest": OLLAMA_DIGEST}],
])
def test_ollama_ambiguous_or_missing_registry_digest_is_never_advertised(models):
    assert discover_cognition_routes(
        command_probe=lambda *_: None,
        ollama_tags=lambda: {"models": models},
        pre_active_models=lambda: None,
    ) == ()


def test_ollama_registry_whitespace_alias_cannot_duplicate_normalized_route():
    routes = discover_cognition_routes(
        command_probe=lambda *_: None,
        ollama_tags=lambda: {"models": [
            {"name": "vera-local:latest", "digest": OLLAMA_DIGEST},
            {"name": " vera-local:latest ", "digest": OLLAMA_DIGEST},
        ]},
        pre_active_models=lambda: None,
    )
    assert len(routes) == 1
    assert routes[0].route_id == "ollama:vera-local:latest"
    assert routes[0].observed_registry_digest == OLLAMA_DIGEST

    no_valid_tags = discover_cognition_routes(
        command_probe=lambda *_: None,
        ollama_tags=lambda: {"models": [{
            "name": " vera-local:latest ", "digest": OLLAMA_DIGEST,
        }]},
        pre_active_models=lambda: None,
    )
    assert no_valid_tags == ()


def test_missing_local_models_and_codex_produce_no_available_routes() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: None,
        pre_active_models=lambda: None,
    )
    assert routes == ()


def test_selection_prefers_eligible_local_no_paid_route_deterministically() -> None:
    routes = (
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
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
            preference=20,
        ),
        CognitionRoute(
            route_id="ollama:qwen3:8b",
            provider="ollama",
            model_or_agent="qwen3:8b",
            local=True,
            available=True,
            current=True,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
            preference=20,
        ),
        CognitionRoute(
            route_id="ollama:vera-local:latest",
            provider="ollama",
            model_or_agent="vera-local:latest",
            local=True,
            available=True,
            current=True,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
            preference=10,
        ),
    )

    selected = select_cognition_route(
        CognitionRequest(required_capabilities=("text",)),
        routes,
    )
    assert selected is not None
    assert selected.route_id == "ollama:vera-local:latest"


def test_codex_requires_explicit_route_authority_before_selection() -> None:
    codex = CognitionRoute(
        route_id="codex:cli",
        provider="codex",
        model_or_agent="codex-cli",
        local=False,
        available=True,
        current=True,
        capabilities=("text",),
        incremental_paid_compute=None,
        auto_admissible=False,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        preference=1,
    )
    request = CognitionRequest(required_capabilities=("text",))

    assert select_cognition_route(request, (codex,)) is None
    assert (
        select_cognition_route(
            request,
            (codex,),
            authorized_route_ids=frozenset({"codex:cli"}),
        )
        == codex
    )


def test_unavailable_stale_or_insufficient_routes_are_not_selected() -> None:
    routes = (
        CognitionRoute(
            route_id="ollama:offline",
            provider="ollama",
            model_or_agent="offline",
            local=True,
            available=False,
            current=True,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        ),
        CognitionRoute(
            route_id="ollama:stale",
            provider="ollama",
            model_or_agent="stale",
            local=True,
            available=True,
            current=False,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        ),
        CognitionRoute(
            route_id="ollama:no-tools",
            provider="ollama",
            model_or_agent="no-tools",
            local=True,
            available=True,
            current=True,
            capabilities=("text",),
            incremental_paid_compute=False,
            auto_admissible=True,
            effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        ),
    )

    assert (
        select_cognition_route(
            CognitionRequest(required_capabilities=("text", "tools")),
            routes,
        )
        is None
    )
