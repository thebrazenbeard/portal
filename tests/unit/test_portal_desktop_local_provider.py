"""Source-level contracts for the direct numeric-loopback cognition transport."""

import json

import pytest

from portal import desktop_cognition, desktop_runtime
from portal.desktop_cognition import CognitionRoute

OLLAMA_DIGEST = "a" * 64


def _route(provider="pre_active_local", model="vera-v10r3-step20", *, registry_digest=None):
    fields = ({"observed_registry_digest": registry_digest or OLLAMA_DIGEST}
              if provider == "ollama" else {})
    return CognitionRoute(
        route_id=f"{provider}:{model}", provider=provider, model_or_agent=model,
        local=True, available=True, current=True, capabilities=("text",),
        incremental_paid_compute=False, auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        **fields,
    )


def _answer(**extra):
    return {"model": "vera-v10r3-step20", "choices": [{
        "message": {"role": "assistant", "content": "trained vera answer"},
        "finish_reason": "stop",
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

        def close(self):
            seen["response_closed"] = True

    class Connection:
        def __init__(self, host, port, timeout):
            seen["connections"].append((host, port, timeout))

        def request(self, method, path, body, headers):
            seen["request"] = (method, path, body, headers)

        def getresponse(self):
            return Response()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(desktop_runtime, "_DeadlineHTTPConnection", Connection)
    return seen


def _stub_ollama(monkeypatch, body, *, digests=(OLLAMA_DIGEST, OLLAMA_DIGEST),
                 tag_payloads=None):
    """One real route shape over a deterministic GET, POST, GET loopback sequence."""
    seen = {"connections": [], "requests": []}
    tag_payloads = list(tag_payloads) if tag_payloads is not None else [
        {"models": [{"name": "vera-local:latest", "digest": digest}]}
        for digest in digests
    ]

    class Response:
        status = 200

        def __init__(self, payload):
            self.encoded = json.dumps(payload).encode("utf-8")

        def read(self, limit):
            return self.encoded[:limit]

        def close(self):
            pass

    class Connection:
        def __init__(self, host, port, timeout):
            seen["connections"].append((host, port, timeout))

        def request(self, method, path, body=None, headers=None):
            seen["requests"].append((method, path, body, headers))
            self.path = path

        def getresponse(self):
            if self.path == "/api/tags":
                return Response(tag_payloads.pop(0))
            return Response(body)

        def close(self):
            pass

    monkeypatch.setattr(desktop_runtime, "_DeadlineHTTPConnection", Connection)
    monkeypatch.setattr(desktop_cognition, "_DeadlineHTTPConnection", Connection)
    return seen


def test_pre_active_local_uses_direct_loopback_and_exact_model(monkeypatch):
    seen = _stub(monkeypatch, _answer())
    assert desktop_runtime.invoke_local_text(_route(), "hello") == "trained vera answer"
    assert seen["connections"] == [("127.0.0.1", 18081, 180.0)]
    method, path, encoded, headers = seen["request"]
    assert method == "POST" and path == "/v1/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(encoded) == {"model": "vera-v10r3-step20", "messages": [{
        "role": "user", "content": "hello",
    }]}
    assert seen["closed"] is True
    assert seen["response_closed"] is True


def test_opt_in_pre_active_chat_sends_role_structured_history(monkeypatch):
    seen = _stub(monkeypatch, _answer())
    messages = [{"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "next"}]
    assert desktop_runtime.invoke_local_messages(_route(), messages) == "trained vera answer"
    assert json.loads(seen["request"][2])["messages"] == messages


def test_opt_in_ollama_chat_uses_api_chat_and_rejects_tool_response(monkeypatch):
    route = _route("ollama", "vera-local:latest")
    messages = [{"role": "user", "content": "hello"}]
    seen = _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                                     "message": {"role": "assistant", "content": "answer"}})
    assert desktop_runtime.invoke_local_messages(route, messages) == "answer"
    assert [request[1] for request in seen["requests"]] == [
        "/api/tags", "/api/chat", "/api/tags",
    ]
    assert json.loads(seen["requests"][1][2])["messages"] == messages
    _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                             "message": {"role": "assistant", "content": "unsafe", "tool_calls": []}})
    with pytest.raises(RuntimeError, match="tool call"):
        desktop_runtime.invoke_local_messages(route, messages)


def test_ollama_uses_direct_loopback(monkeypatch):
    seen = _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                                     "response": "ollama answer"})
    assert desktop_runtime.invoke_local_text(_route("ollama", "vera-local:latest"), "hello") == "ollama answer"
    assert seen["connections"] == [("127.0.0.1", 11434, 2.0),
                                   ("127.0.0.1", 11434, 120.0),
                                   ("127.0.0.1", 11434, 2.0)]
    assert [request[1] for request in seen["requests"]] == [
        "/api/tags", "/api/generate", "/api/tags",
    ]
    assert json.loads(seen["requests"][1][2]) == {
        "model": "vera-local:latest", "prompt": "hello", "stream": False,
    }


@pytest.mark.parametrize("invoke", [
    lambda route: desktop_runtime.invoke_local_text(route, "hello"),
    lambda route: desktop_runtime.invoke_local_messages(route, [{"role": "user", "content": "hello"}]),
])
def test_ollama_tag_changed_before_inference_never_posts(monkeypatch, invoke):
    seen = _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                                     "response": "forged", "message": {"role": "assistant", "content": "forged"}},
                        digests=("b" * 64,))
    with pytest.raises(RuntimeError, match="registry identity changed"):
        invoke(_route("ollama", "vera-local:latest"))
    assert [request[1] for request in seen["requests"]] == ["/api/tags"]


@pytest.mark.parametrize("invoke,expected_post", [
    (lambda route: desktop_runtime.invoke_local_text(route, "hello"), "/api/generate"),
    (lambda route: desktop_runtime.invoke_local_messages(route, [{"role": "user", "content": "hello"}]), "/api/chat"),
])
def test_ollama_tag_changed_during_inference_discards_answer(monkeypatch, invoke, expected_post):
    seen = _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                                     "response": "forged", "message": {"role": "assistant", "content": "forged"}},
                        digests=(OLLAMA_DIGEST, "b" * 64))
    with pytest.raises(RuntimeError, match="registry identity changed"):
        invoke(_route("ollama", "vera-local:latest"))
    assert [request[1] for request in seen["requests"]] == [
        "/api/tags", expected_post, "/api/tags",
    ]


@pytest.mark.parametrize("tag_payload", [
    {"models": [{"name": "vera-local:latest"}]},
    {"models": [{"name": "vera-local:latest", "digest": "untrusted"}]},
    {"models": [{"name": "vera-local:latest", "digest": OLLAMA_DIGEST}] * 2},
])
def test_ollama_registry_missing_invalid_or_duplicate_fails_before_post(monkeypatch, tag_payload):
    seen = _stub_ollama(monkeypatch, {"model": "vera-local:latest", "done": True,
                                     "response": "must-not-return"}, tag_payloads=[tag_payload])
    with pytest.raises(RuntimeError, match="registry identity changed"):
        desktop_runtime.invoke_local_text(_route("ollama", "vera-local:latest"), "hello")
    assert [request[1] for request in seen["requests"]] == ["/api/tags"]


def test_ollama_without_qualified_registry_digest_cannot_infer(monkeypatch):
    route = _route("ollama", "vera-local:latest", registry_digest="invalid")
    monkeypatch.setattr(desktop_runtime, "_DeadlineHTTPConnection",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")))
    with pytest.raises(RuntimeError, match="registry digest required"):
        desktop_runtime.invoke_local_text(route, "hello")


def test_proxy_environment_cannot_redirect_prompt(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example:8080")
    seen = _stub(monkeypatch, _answer())
    desktop_runtime.invoke_local_text(_route(), "private prompt")
    assert seen["connections"] == [("127.0.0.1", 18081, 180.0)]


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
    _answer(choices=[{"message": {"role": "assistant", "content": "hi", "tool_calls": [{}]}, "finish_reason": "stop"}]),
    _answer(choices=[{"message": {"role": "assistant", "content": "hi", "function_call": {}}, "finish_reason": "stop"}]),
    _answer(choices=[{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "tool_calls"}]),
    _answer(choices=[{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "function_call"}]),
    _answer(tool_calls=[{}]),
])
def test_tool_call_responses_are_rejected(monkeypatch, body):
    _stub(monkeypatch, body)
    with pytest.raises(RuntimeError, match="tool call"):
        desktop_runtime.invoke_local_text(_route(), "hello")


@pytest.mark.parametrize("role", ["tool", "system", "user", "", None])
def test_pre_active_requires_assistant_role(monkeypatch, role):
    _stub(monkeypatch, _answer(choices=[{
        "message": {"role": role, "content": "spoofed answer"}, "finish_reason": "stop",
    }]))
    with pytest.raises(RuntimeError, match="role is not assistant"):
        desktop_runtime.invoke_local_text(_route(), "hello")


@pytest.mark.parametrize("reason", [None, "content_filter", "function_call", "length", "unexpected"])
def test_pre_active_requires_completed_text_finish_reason(monkeypatch, reason):
    _stub(monkeypatch, _answer(choices=[{
        "message": {"role": "assistant", "content": "answer"}, "finish_reason": reason,
    }]))
    with pytest.raises(RuntimeError, match="tool call" if reason == "function_call" else "did not finish normally"):
        desktop_runtime.invoke_local_text(_route(), "hello")


@pytest.mark.parametrize("choices", [[], [
    {"message": {"role": "assistant", "content": "one"}, "finish_reason": "stop"},
    {"message": {"role": "assistant", "content": "two"}, "finish_reason": "stop"},
]])
def test_pre_active_requires_exactly_one_choice(monkeypatch, choices):
    _stub(monkeypatch, _answer(choices=choices))
    with pytest.raises(RuntimeError, match="exactly one choice"):
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
        desktop_runtime, "_DeadlineHTTPConnection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )
    with pytest.raises(ValueError, match="prompt"):
        desktop_runtime.invoke_local_text(_route(), task)


def test_unknown_provider_fails_closed():
    with pytest.raises(RuntimeError, match="no resident adapter"):
        desktop_runtime.invoke_local_text(_route("unknown", "x"), "hello")
