"""Optional real cross-repository Rezon gateway/PORTAL observer contract.

Run with Rezon checkout installed or on PYTHONPATH; otherwise skip rather than
substitute a fake server for an independently developed peer implementation.
"""

from __future__ import annotations

import json
from threading import Thread

import pytest

from portal.desktop_cognition import CognitionRequest, discover_cognition_routes, select_cognition_route
from portal.desktop_rezon import authorized_rezon_route_ids
from portal.desktop_rezon import discover_rezon_observer
from portal.desktop_runtime import invoke_local_text


def test_real_rezon_hmac_observer_through_portal_route(tmp_path, monkeypatch):
    pytest.importorskip("rezon.collective")
    from rezon.collective import CollectiveStore
    from rezon.collective_http import make_server

    store = CollectiveStore(tmp_path / "collective.sqlite3")
    episode = store.create("Compare two claims", ("alice", "bob"))
    token = "0123456789abcdef" * 4  # disposable test credential only
    server = make_server(
        store, "test-admin-secret-at-least-16", {"alice": "test-alice-secret-at-least-16",
                                                "bob": "test-bob-secret-at-least-16"},
        read_token=token,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PORTAL_REZON_EPISODE_ID", episode)
    monkeypatch.setenv("PORTAL_REZON_ADMIT_EPISODE_ID", episode)
    monkeypatch.setenv("PORTAL_REZON_PORT", str(server.server_port))
    monkeypatch.setenv("REZON_COLLECTIVE_STATE_READ_TOKEN", token)
    try:
        def routes():
            return discover_cognition_routes(command_probe=lambda *_: None,
                ollama_tags=lambda: None, pre_active_models=lambda: None,
                rezon_observer=discover_rezon_observer)

        # Even an authenticated partial round cannot advertise a synthesis.
        assert routes() == ()
        store.submit_answer(episode, "alice", "First unverified claim")
        store.submit_answer(episode, "bob", "Second unverified claim")
        store.save_synthesis(episode, "integrator", store.snapshot(episode).digest,
                             "Neither claim is independently verified")
        discovered = routes()
        route = select_cognition_route(CognitionRequest(
            required_capabilities=("rezon_collective_advisory",)), discovered,
            authorized_route_ids=authorized_rezon_route_ids())
        assert route is not None
        assert select_cognition_route(CognitionRequest(), discovered,
                                     authorized_route_ids=authorized_rezon_route_ids()) is None
        result = json.loads(invoke_local_text(route, episode))
        assert result["content"] == "Neither claim is independently verified"
        assert result["synthesis_status"] == "unverified_model_synthesis"
        assert result["independent_hosts_verified"] is False
        assert result["protected_effect_authority"] is False
        # A durable Rezon change after route observation must reject stale use.
        answers = store.snapshot(episode).answers
        store.submit_review(episode, "alice", "Concern about evidence",
                            target_ids=(answers[1].contribution_id,))
        with pytest.raises(RuntimeError, match="changed"):
            invoke_local_text(route, episode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
