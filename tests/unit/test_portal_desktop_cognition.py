from __future__ import annotations

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


def test_missing_ollama_and_codex_produce_no_available_routes() -> None:
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: None,
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
