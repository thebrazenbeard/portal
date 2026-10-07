from dataclasses import replace
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request

import pytest

import portal.desktop_cognition as cognition
import portal.desktop_runtime as runtime
from portal.desktop_cognition import CognitionRequest, CognitionRoute, discover_cognition_routes, select_cognition_route
from portal.desktop_runtime import CognitionLedger, CognitionRequestEnvelope, ResidentCognitionEngine


def route(**changes):
    return replace(CognitionRoute(
        route_id="ollama:local:latest", provider="ollama", model_or_agent="local:latest",
        local=True, available=True, current=True, capabilities=("text",),
        incremental_paid_compute=False, auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
    ), **changes)


def test_discovery_requires_explicit_cognition_policy():
    routes = cognition.discover_cognition_routes(
        command_probe=lambda *_: None,
        ollama_tags=lambda: {"models": [{"name": "local:latest"}]},
        pre_active_models=lambda: None,
    )
    assert routes[0].auto_admissible is False
    assert cognition.select_cognition_route(CognitionRequest(), routes) is None


def test_expired_or_future_route_is_not_selected():
    assert cognition.select_cognition_route(CognitionRequest(), (route(observed_at=10, expires_at=20),), now=20) is None
    assert cognition.select_cognition_route(CognitionRequest(), (route(observed_at=30, expires_at=40),), now=20) is None


def test_route_authority_does_not_grant_paid_compute():
    paid = route(local=False, incremental_paid_compute=True, auto_admissible=False)
    assert cognition.select_cognition_route(CognitionRequest(), (paid,), authorized_route_ids=frozenset({paid.route_id})) is None


def test_ollama_remapped_to_remote_is_not_invoked(monkeypatch):
    requests = []
    def transport(request, *, timeout):
        requests.append(request.full_url)
        return {"models": [{"name": "local:latest", "digest": "sha256:a", "remote_host": "https://ollama.com"}]}
    monkeypatch.setattr(runtime, "loopback_json", transport, raising=False)
    with pytest.raises(RuntimeError, match="local"):
        runtime.invoke_local_text(route(observed_version="sha256:a"), "hello")
    assert not any(url.endswith("/generate") for url in requests)


def test_ollama_digest_drift_is_not_invoked(monkeypatch):
    def transport(request, *, timeout):
        return {"models": [{"name": "local:latest", "digest": "sha256:b"}]}
    monkeypatch.setattr(runtime, "loopback_json", transport, raising=False)
    with pytest.raises(RuntimeError, match="drift"):
        runtime.invoke_local_text(route(observed_version="sha256:a"), "hello")


@pytest.mark.parametrize("target", ["https://api.example.com/v1", "http://127.0.0.1:1/v1?token=x"])
def test_direct_adapter_rejects_nonlocal_or_ambiguous_target(target):
    with pytest.raises((ValueError, RuntimeError), match="loopback|credentials|query"):
        runtime.invoke_local_text(route(provider="pre_active_target", base_url=target), "hello")


def test_rejected_acceptance_retries_acceptance_without_duplicate_model_call(tmp_path):
    calls = []
    decisions = iter([{"status": "REJECTED"}, {"status": "ACCEPTED_HOST_OBSERVATION"}])
    ledger = CognitionLedger(tmp_path / "cognition.db")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (route(),),
            invoke_route=lambda *_: calls.append("model") or "result",
            accept_result=lambda *_: next(decisions),
        )
        request = CognitionRequestEnvelope("r1", "HUMAN", "test", "hello", 100)
        first = engine.process(request)
        assert first.state == "FAILED" and first.retryable
        assert ledger.get("r1")["response_text"] == "result"
        assert engine.process(request).state == "COMPLETED"
        assert calls == ["model"]
    finally:
        ledger.close()


def test_real_local_transport_disables_proxies_and_redirects(monkeypatch):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://api.example.com/models")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert cognition.loopback_json(urllib.request.Request(base + "/models"), timeout=2) == {"ok": True}
        with pytest.raises(RuntimeError, match="redirect"):
            cognition.loopback_json(urllib.request.Request(base + "/redirect"), timeout=2)
        assert hits == ["/models", "/redirect"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_discovery_does_not_grant_local_cognition_authority_without_policy() -> None:
    routes = discover_cognition_routes(pre_active_models=lambda: None, now=100,
        command_probe=lambda *_args: None,
        ollama_tags=lambda: {"models": [{"name": "local-model:latest"}]},
    )
    assert len(routes) == 1
    assert routes[0].available
    assert routes[0].auto_admissible is False
    assert select_cognition_route(CognitionRequest(), routes) is None


@pytest.mark.parametrize("model", [
    {"name": "remote:cloud"},
    {"name": "remote:latest", "remote_host": "https://ollama.com"},
    {"name": "remote:latest", "remote_model": "remote-model"},
])
def test_local_ollama_endpoint_does_not_make_cloud_model_free_or_local(model) -> None:
    routes = discover_cognition_routes(pre_active_models=lambda: None, now=100,
        command_probe=lambda *_args: None,
        ollama_tags=lambda: {"models": [model]},
        allow_local_no_paid_compute=True,
    )
    assert len(routes) == 1
    assert routes[0].local is False
    assert routes[0].incremental_paid_compute is None
    assert routes[0].auto_admissible is False
    assert select_cognition_route(CognitionRequest(), routes) is None


def test_ollama_inventory_is_deduplicated_and_stable() -> None:
    models = [
        {"name": " a:latest ", "digest": "sha256:a"},
        {"name": "a:latest", "digest": "sha256:a"},
        {"name": "b:latest", "digest": "sha256:b"},
        {"name": ""}, None,
    ]
    first = discover_cognition_routes(pre_active_models=lambda: None, now=100, command_probe=lambda *_args: None, ollama_tags=lambda: {"models": models})
    second = discover_cognition_routes(pre_active_models=lambda: None, now=100, command_probe=lambda *_args: None, ollama_tags=lambda: {"models": list(reversed(models))})
    assert first == second
    assert [route.route_id for route in first] == ["ollama:a:latest", "ollama:b:latest"]
    assert first[0].observed_version == "sha256:a"


def test_installed_ollama_without_api_is_recorded_unavailable() -> None:
    routes = discover_cognition_routes(pre_active_models=lambda: None, now=100,
        command_probe=lambda name, _args: "ollama version 1.2.3" if name == "ollama" else None,
        ollama_tags=lambda: None,
    )
    assert len(routes) == 1
    assert routes[0].provider == "ollama"
    assert routes[0].available is False
    assert routes[0].auto_admissible is False
    assert routes[0].observed_version == "ollama version 1.2.3"


@pytest.mark.parametrize("changes", [
    {"local": False},
    {"incremental_paid_compute": True},
    {"incremental_paid_compute": None},
    {"effect_authority_ceiling": "UNRESTRICTED"},
])
def test_auto_admissible_flag_cannot_bypass_cost_or_effect_authority(changes) -> None:
    route = CognitionRoute(
        route_id="injected:route", provider="injected", model_or_agent="model",
        local=True, available=True, current=True, capabilities=("text",),
        incremental_paid_compute=False, auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
    )
    assert select_cognition_route(CognitionRequest(), (replace(route, **changes),)) is None
