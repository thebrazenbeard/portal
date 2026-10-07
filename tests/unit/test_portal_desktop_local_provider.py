from __future__ import annotations

import io
import json
import urllib.request

from portal.desktop_cognition import CognitionRoute
from portal.desktop_runtime import invoke_local_text


def _route(
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> CognitionRoute:
    return CognitionRoute(
        route_id=f"{provider}:{model}",
        provider=provider,
        model_or_agent=model,
        local=True,
        available=True,
        current=True,
        capabilities=("text",),
        incremental_paid_compute=False,
        auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        base_url=base_url,
        api_key_env=api_key_env,
    )


def test_pre_active_local_invocation_uses_openai_compatible_loopback(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "trained vera answer",
                            }
                        }
                    ]
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    answer = invoke_local_text(
        _route("pre_active_local", "vera-v10r3-step20"),
        "hello",
    )

    assert answer == "trained vera answer"
    assert observed["url"] == "http://127.0.0.1:18081/v1/chat/completions"
    assert observed["payload"] == {
        "model": "vera-v10r3-step20",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_pre_active_target_invocation_uses_bound_loopback_base_url(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        observed["url"] = request.full_url
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "bound target answer",
                            }
                        }
                    ]
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    answer = invoke_local_text(
        _route(
            "pre_active_target",
            "qwen3.5-4b-local",
            base_url="http://127.0.0.1:19081/v1",
        ),
        "hello",
    )

    assert answer == "bound target answer"
    assert observed["url"] == "http://127.0.0.1:19081/v1/chat/completions"
    assert observed["payload"] == {
        "model": "qwen3.5-4b-local",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_pre_active_target_invocation_uses_bound_api_key_environment(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        observed["authorization"] = request.headers.get("Authorization")
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "authenticated answer",
                            }
                        }
                    ]
                }
            ).encode("utf-8")
        )

    monkeypatch.setenv("VERA_LOCAL_MODEL_TOKEN", "secret-token")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    answer = invoke_local_text(
        _route(
            "pre_active_target",
            "qwen3.5-4b-local",
            base_url="http://127.0.0.1:18081/v1",
            api_key_env="VERA_LOCAL_MODEL_TOKEN",
        ),
        "hello",
    )

    assert answer == "authenticated answer"
    assert observed["authorization"] == "Bearer secret-token"


def test_ollama_invocation_remains_supported(monkeypatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float):
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        return io.BytesIO(
            json.dumps({"done": True, "response": "ollama answer"}).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert invoke_local_text(
        _route("ollama", "vera-local:latest"),
        "hello",
    ) == "ollama answer"


def test_unknown_local_provider_fails_closed() -> None:
    try:
        invoke_local_text(_route("unknown", "x"), "hello")
    except RuntimeError as exc:
        assert "no resident adapter" in str(exc)
    else:
        raise AssertionError("unknown provider should fail")
