from __future__ import annotations

from pathlib import Path

import portal.desktop_host as desktop_host
from portal.desktop_host import (
    claim_bridge_request,
    prepare_runtime_layout,
)


def test_prepare_runtime_layout_creates_component_state_and_bridge_directories(tmp_path: Path) -> None:
    layout = prepare_runtime_layout(tmp_path)

    assert layout["state_root"] == tmp_path / "state"
    for component in ("vera", "portal", "pre-active", "cognition"):
        assert (tmp_path / "state" / component).is_dir()
    assert (tmp_path / "bridge" / "requests").is_dir()
    assert (tmp_path / "bridge" / "claimed").is_dir()
    assert (tmp_path / "bridge" / "responses").is_dir()


def test_claim_bridge_request_moves_request_before_processing(tmp_path: Path) -> None:
    layout = prepare_runtime_layout(tmp_path)
    request_path = layout["requests"] / "r1.json"
    request_path.write_text('{"request_id":"r1"}', encoding="utf-8")

    claimed_path = claim_bridge_request(
        request_path,
        layout["claimed"],
    )

    assert claimed_path == layout["claimed"] / "r1.json"
    assert claimed_path.is_file()
    assert not request_path.exists()


def test_resident_route_discovery_uses_only_the_active_pre_active_target() -> None:
    discover = getattr(desktop_host, "discover_resident_cognition_routes", None)
    assert callable(discover)

    class FakeStore:
        def get_active_model_target(self):
            return {
                "name": "vera-base",
                "provider": "openai-compatible",
                "base_url": "http://127.0.0.1:18081/v1",
                "model": "qwen3.5-4b-local",
                "api_key_env": None,
                "active": True,
            }

    expected_target = {
        "name": "vera-base",
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:18081/v1",
        "model": "qwen3.5-4b-local",
        "api_key_env": None,
    }
    routes = discover(
        FakeStore(),
        expected_target=expected_target,
        models_probe=lambda _base_url: {
            "object": "list",
            "data": [{"id": "qwen3.5-4b-local", "object": "model"}],
        },
    )

    assert [route.route_id for route in routes] == [
        "preactive-target:vera-base"
    ]
    assert all(route.provider != "ollama" for route in routes)


def test_resident_route_discovery_fails_closed_if_active_target_drifted() -> None:
    class FakeStore:
        def get_active_model_target(self):
            return {
                "name": "vera-trained",
                "provider": "openai-compatible",
                "base_url": "http://127.0.0.1:18081/v1",
                "model": "vera-v10r3-step20",
                "api_key_env": None,
                "active": True,
            }

    routes = desktop_host.discover_resident_cognition_routes(
        FakeStore(),
        expected_target={
            "name": "vera-base",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "qwen3.5-4b-local",
            "api_key_env": None,
        },
        models_probe=lambda _base_url: {
            "object": "list",
            "data": [{"id": "vera-v10r3-step20", "object": "model"}],
        },
    )

    assert routes == ()


def test_runtime_id_changes_when_bound_cognition_target_changes() -> None:
    base_sources = [
        {
            "component": "portal",
            "repository": "thebrazenbeard/portal",
            "ref": "main",
            "sha": "a" * 40,
        }
    ]
    first = {
        "sources": base_sources,
        "cognition_target": {
            "name": "vera-base",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "qwen3.5-4b-local",
            "api_key_env": None,
        },
    }
    second = {
        "sources": base_sources,
        "cognition_target": {
            "name": "vera-trained",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:18081/v1",
            "model": "vera-v10r3-step20",
            "api_key_env": None,
        },
    }

    assert desktop_host._runtime_id(first) != desktop_host._runtime_id(second)
