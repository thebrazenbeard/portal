"""Bounded, opt-in local TEXT inference research transport (never an effect executor).

This is an uninstalled PORTAL acceptance donor for Integration, NOT an
automatically selected Vera cognition provider. No model is invoked by tests:
all HTTP happens against an injected fake opener. The live pilot must remain
unmodified. The caller-controlled permit is a *test opt-in*, not an authority
token, consent to provider changes, or a production permission.

WARNING: GET /api/tags and later POST /api/generate do not atomically bind
the model's underlying bytes; alias/registry TOCTOU remains unqualified.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import URLError

MAX_PROMPT_BYTES = 4096
MAX_OUTPUT_BYTES = 4096
MAX_REPLY_BYTES = 16384
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
EXACT_TEST_SCOPE = "SYNTHETIC_LOCAL_INFERENCE_TEST"


class CognitionBoundaryError(RuntimeError):
    """Request denied or evidence insufficient. Never retries implicitly."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CognitionBoundaryError("redirect denied")


@dataclass(frozen=True, slots=True)
class InferencePermit:
    model: str
    digest: str
    scope: str


@dataclass(frozen=True, slots=True)
class LocalTextReceipt:
    text: str
    model: str
    reported_model_digest: str
    prompt_sha256: str
    response_sha256: str
    claim_ceiling: str = "LOCAL_MODEL_TEXT_UNTRUSTED"
    runtime_consumption_verified: bool = False
    authorized_tool_effect: bool = False
    model_byte_identity_verified: bool = False
    independent_review_verified: bool = False


class LocalCognitionGate:
    """Local-only text gateway with explicit in-process opt-in (not authority)."""

    def __init__(self, *, model: str, expected_digest: str, port: int = 11434,
                 opener=None, armed: bool = False, timeout: float = 5):
        if type(model) is not str or not MODEL.fullmatch(model):
            raise ValueError("model name must be a strict local registry tag")
        if type(expected_digest) is not str or not DIGEST.fullmatch(expected_digest):
            raise ValueError("expected model digest must be lowercase SHA-256")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an exact local TCP port")
        if type(armed) is not bool:
            raise ValueError("armed must be a boolean")
        if type(timeout) not in (float, int) or not 0 < timeout <= 10:
            raise ValueError("timeout must be positive and bounded")
        self.model = model
        self.expected_digest = expected_digest
        self.port = port
        self.armed = armed
        self.timeout = timeout
        self.opener = opener if opener is not None else build_opener(
            ProxyHandler({}), NoRedirect()
        )
        self.endpoint = f"http://127.0.0.1:{port}"

    def _json(self, request: Request):
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise CognitionBoundaryError("model service response not HTTP 200")
                data = response.read(MAX_REPLY_BYTES + 1)
            if len(data) > MAX_REPLY_BYTES:
                raise CognitionBoundaryError("model service response too large")
            value = json.loads(data.decode("utf-8"))
            if type(value) is not dict:
                raise CognitionBoundaryError("model service JSON is not an object")
            return value
        except CognitionBoundaryError:
            raise
        except (URLError, OSError, ValueError, UnicodeError,
                TimeoutError, TypeError) as exc:
            raise CognitionBoundaryError("model service read failed") from exc

    def generate(self, prompt: str, *, permit: InferencePermit | None = None,
                 max_tokens: int = 64) -> LocalTextReceipt:
        # Verify every local input and opt-in BEFORE any network activity.
        if not self.armed:
            raise CognitionBoundaryError("gate disarmed")
        if (type(permit) is not InferencePermit
                or permit.model != self.model
                or permit.digest != self.expected_digest
                or permit.scope != EXACT_TEST_SCOPE):
            raise CognitionBoundaryError("explicit matching synthetic test permit required")
        if type(prompt) is not str or not prompt or len(
            prompt.encode("utf-8")
        ) > MAX_PROMPT_BYTES:
            raise CognitionBoundaryError("invalid or oversized text prompt")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 128:
            raise CognitionBoundaryError("generation token cap invalid")

        tags = self._json(Request(
            self.endpoint + "/api/tags", method="GET",
            headers={"Accept": "application/json"}
        ))
        models = tags.get("models")
        if type(models) is not list or len(models) > 256:
            raise CognitionBoundaryError("model inventory unavailable or unbounded")
        matches = [item for item in models
                   if type(item) is dict and item.get("name") == self.model]
        if (len(matches) != 1
                or matches[0].get("digest") != self.expected_digest):
            raise CognitionBoundaryError("model digest changed or no unique tag match")

        body = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": 0,
            "options": {"num_predict": max_tokens, "temperature": 0},
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        result = self._json(Request(
            self.endpoint + "/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        ))
        text = result.get("response")
        if (result.get("model") != self.model
                or result.get("done") is not True
                or type(text) is not str
                or len(text.encode("utf-8")) > MAX_OUTPUT_BYTES):
            raise CognitionBoundaryError("model text response is invalid or mismatched")
        return LocalTextReceipt(
            text=text, model=self.model,
            reported_model_digest=self.expected_digest,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
