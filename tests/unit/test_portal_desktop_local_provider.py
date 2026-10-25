"""Source-level contracts for the direct numeric-loopback cognition transport."""

import json

import pytest

from portal import desktop_runtime
from portal.desktop_cognition import CognitionRoute


def _route(provider="pre_active_local", model="vera-v10r3-step20"):
    return CognitionRoute(
        route_id=f"{provider}:{model}", provider=provider, model_or_agent=model,
        local=True, available=True, current=True, capabilities=("text",),
        incremental_paid_compute=False, auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
    )


def _answer(**extra):
    return {"model": "vera-v10r3-step20", "choices": [{
        "message": {"role": "assistant", "content": "trained vera answer"},
    }], **extra}


def _stub(monkeypatch, body, status=200):
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    seen = {"connections": []}

    class Response:
        def __init__(self):
            self.status = status

        def read(self, limit):
            seen["read_limit"] = limit
            return data[:limit]

    class Connection:
        def __init__(self, host, port, timeout):
            seen["connections"].append((host, port, timeout))

        def request(self, method, path, body, headers):
            seen["request"] = (method, path, body, headers)

        def getresponse(self):
            return Response()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(desktop_runtime.http.client, "HTTPConnection", Connection)
    return seen


def test_pre_active_local_uses_direct_loopback_and_exact_model(monkeypatch):
    seen = _stub(monkeypatch, _answer())
    assert desktop_runtime.invoke_local_text(_route(), "hello") == "trained vera answer"
    assert seen["connections"] == [("127.0.0.1", 18081, 90.0)]
    method, path, encoded, headers = seen["request"]
    assert method == "POST" and path == "/v1/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(encoded) == {"model": "vera-v10r3-step20", "messages": [{
        "role": "user", "content": "hello",
    }]}
    assert seen["closed"] is True


def test_ollama_uses_direct_loopback(monkeypatch):
    seen = _stub(monkeypatch, {"model": "vera-local:latest", "done": True,
                              "response": "ollama answer"})
    assert desktop_runtime.invoke_local_text(_route("ollama", "vera-local:latest"), "hello") == "ollama answer"
    assert seen["connections"] == [("127.0.0.1", 11434, 60.0)]
    assert seen["request"][1] == "/api/generate"
    assert json.loads(seen["request"][2]) == {
        "model": "vera-local:latest", "prompt": "hello", "stream": False,
    }


def test_proxy_environment_cannot_redirect_prompt(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example:8080")
    seen = _stub(monkeypatch, _answer())
    desktop_runtime.invoke_local_text(_route(), "private prompt")
    assert seen["connections"] == [("127.0.0.1", 18081, 90.0)]


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 503])
def test_redirects_and_errors_fail_closed_without_second_request(monkeypatch, status):
    seen = _stub(monkeypatch, {}, status=status)
    with pytest.raises(RuntimeError, match=f"HTTP status {status}"):
        desktop_runtime.invoke_local_text(_route(), "hello")
    assert len(seen["connections"]) == 1 and seen["closed"] is True


@pytest.mark.parametrize("body", [
    {"choices": [{"message": {"content": "hi"}}]},
    _answer(model="other-model"),
])
def test_missing_or_wrong_model_echo_is_rejected(monkeypatch, body):
    _stub(monkeypatch, body)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        desktop_runtime.invoke_local_text(_route(), "hello")


@pytest.mark.parametrize("body", [
    _answer(choices=[{"message": {"content": "hi", "tool_calls": [{}]}}]),
    _answer(choices=[{"message": {"content": "hi"}, "finish_reason": "tool_calls"}]),
    _answer(tool_calls=[{}]),
])
def test_tool_call_responses_are_rejected(monkeypatch, body):
    _stub(monkeypatch, body)
    with pytest.raises(RuntimeError, match="tool call"):
        desktop_runtime.invoke_local_text(_route(), "hello")


def test_response_size_is_bounded(monkeypatch):
    seen = _stub(monkeypatch, b"x" * (desktop_runtime._MAX_LOCAL_RESPONSE_BYTES + 1))
    with pytest.raises(RuntimeError, match="size limit"):
        desktop_runtime.invoke_local_text(_route(), "hello")
    assert seen["read_limit"] == desktop_runtime._MAX_LOCAL_RESPONSE_BYTES + 1


def test_invalid_utf8_json_rejected(monkeypatch):
    _stub(monkeypatch, b"\xff")
    with pytest.raises(RuntimeError, match="valid UTF-8 JSON"):
        desktop_runtime.invoke_local_text(_route(), "hello")


@pytest.mark.parametrize("task", ["", " ", "x" * (16 * 1024 + 1)])
def test_invalid_prompt_rejected_before_network(monkeypatch, task):
    monkeypatch.setattr(
        desktop_runtime.http.client, "HTTPConnection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )
    with pytest.raises(ValueError, match="prompt"):
        desktop_runtime.invoke_local_text(_route(), task)


def test_unknown_provider_fails_closed():
    with pytest.raises(RuntimeError, match="no resident adapter"):
        desktop_runtime.invoke_local_text(_route("unknown", "x"), "hello")
