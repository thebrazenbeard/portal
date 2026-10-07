from __future__ import annotations

import hashlib
import json
from typing import Any

from .desktop_cognition import CognitionRoute
from .desktop_runtime import CognitionRequestEnvelope


class VeraRuntimeAcceptance:
    """Bind a cognition result to live Vera runtime evidence.

    This is an acceptance receipt for host-observed cognition only. It is not a
    canonical-memory write, lifecycle transition, protected effect, or claim
    that the model route itself is Vera.
    """

    def __init__(self, vera_runtime: Any, *, runtime_id: str) -> None:
        if not runtime_id.strip():
            raise ValueError("runtime_id is required")
        if type(vera_runtime).__name__ != "QualifiedVeraRuntime" and not hasattr(
            vera_runtime, "resume_context"
        ):
            raise TypeError("vera_runtime must expose QualifiedVeraRuntime context")
        self.vera_runtime = vera_runtime
        self.runtime_id = runtime_id

    def __call__(
        self,
        request: CognitionRequestEnvelope,
        route: CognitionRoute,
        response_text: str,
    ) -> dict[str, object]:
        context = self.vera_runtime.resume_context()
        if not isinstance(context, dict):
            raise RuntimeError("QualifiedVeraRuntime resume context must be an object")
        context_bytes = json.dumps(
            context,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        payload = {
            "schema": "VERA_RUNTIME_COGNITION_ACCEPTANCE_V1",
            "status": "ACCEPTED_HOST_OBSERVATION",
            "runtime_id": self.runtime_id,
            "request_id": request.request_id,
            "source": request.source,
            "reason": request.reason,
            "task": request.task,
            "route_id": route.route_id,
            "provider": route.provider,
            "model_or_agent": route.model_or_agent,
            "response_sha256": hashlib.sha256(
                response_text.encode("utf-8")
            ).hexdigest(),
            "context_digest": context_digest,
            "canonical_memory_write": False,
            "protected_effect_authority": False,
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return {**payload, "acceptance_digest": digest}
