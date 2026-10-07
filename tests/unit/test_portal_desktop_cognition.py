from __future__ import annotations

import portal.desktop_cognition as desktop_cognition
from portal.desktop_cognition import (
    CognitionRequest,
    CognitionRoute,
    discover_cognition_routes,
    select_cognition_route,
)


def test_ollama_models_are_discovered_as_local_no_incremental_paid_text_routes() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda name, args: (
            "codex-cli 1.2.3" if name == "codex" else None
        ),
        ollama_tags=lambda: {
            "models": [
                {"name": "vera-local:latest", "modified_at": "2026-10-06T20:00:00Z"},
                {"name": "qwen3:8b", "modified_at": "2026-10-01T12:00:00Z"},
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

    codex = by_id["codex:cli"]
    assert codex.available is True
    assert codex.incremental_paid_compute is None
    assert codex.auto_admissible is False


def test_trained_pre_active_adapter_is_discovered_and_preferred_over_ollama_vera() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: {
            "models": [
                {"name": "vera-local:latest", "modified_at": "2026-08-01T00:00:00Z"},
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
                {"name": "vera-local:latest", "modified_at": "2026-08-01T00:00:00Z"},
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


def test_resident_pre_active_target_discovery_surface_exists() -> None:
    assert callable(getattr(desktop_cognition, "discover_pre_active_target_routes", None))


def test_resident_routes_bind_only_the_active_pre_active_target() -> None:
    routes = desktop_cognition.discover_pre_active_target_routes(
        active_target_probe=lambda: {
            "name": "vera-base",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "qwen3.5-4b-local",
            "api_key_env": None,
            "active": True,
            "updated_at": 100.0,
        },
        models_probe=lambda base_url: {
            "object": "list",
            "data": [{"id": "qwen3.5-4b-local", "object": "model"}],
        },
    )

    assert len(routes) == 1
    route = routes[0]
    assert route.route_id == "preactive-target:vera-base"
    assert route.provider == "pre_active_target"
    assert route.model_or_agent == "qwen3.5-4b-local"
    assert route.base_url == "http://127.0.0.1:18081/v1"
    assert route.local is True
    assert route.auto_admissible is True
    assert route.effect_authority_ceiling == "COGNITION_ONLY_NO_PROTECTED_EFFECT"


def test_resident_routes_fail_closed_without_an_active_or_ready_pre_active_target() -> None:
    assert desktop_cognition.discover_pre_active_target_routes(
        active_target_probe=lambda: None,
        models_probe=lambda _base_url: (_ for _ in ()).throw(
            AssertionError("must not probe without a target")
        ),
    ) == ()

    assert desktop_cognition.discover_pre_active_target_routes(
        active_target_probe=lambda: {
            "name": "vera-base",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "qwen3.5-4b-local",
            "active": True,
        },
        models_probe=lambda _base_url: {
            "object": "list",
            "data": [{"id": "different-model", "object": "model"}],
        },
    ) == ()


def test_resident_target_discovery_uses_bound_api_key_environment(
    monkeypatch,
) -> None:
    import io
    import json
    import urllib.request

    observed: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        observed["authorization"] = request.headers.get("Authorization")
        return io.BytesIO(
            json.dumps(
                {
                    "object": "list",
                    "data": [{"id": "qwen3.5-4b-local", "object": "model"}],
                }
            ).encode("utf-8")
        )

    monkeypatch.setenv("VERA_LOCAL_MODEL_TOKEN", "secret-token")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    routes = desktop_cognition.discover_pre_active_target_routes(
        active_target_probe=lambda: {
            "name": "vera-base",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "qwen3.5-4b-local",
            "api_key_env": "VERA_LOCAL_MODEL_TOKEN",
            "active": True,
        },
    )

    assert len(routes) == 1
    assert routes[0].api_key_env == "VERA_LOCAL_MODEL_TOKEN"
    assert observed["authorization"] == "Bearer secret-token"


def test_resident_routes_reject_non_loopback_targets_even_if_persisted_active() -> None:
    routes = desktop_cognition.discover_pre_active_target_routes(
        active_target_probe=lambda: {
            "name": "unsafe",
            "provider": "openai-compatible",
            "base_url": "https://example.com/v1",
            "model": "remote-model",
            "active": True,
        },
        models_probe=lambda _base_url: {
            "object": "list",
            "data": [{"id": "remote-model", "object": "model"}],
        },
    )

    assert routes == ()
