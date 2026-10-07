from __future__ import annotations

from portal.desktop_cognition import CognitionRoute
from portal.desktop_runtime import CognitionRequestEnvelope
from portal.desktop_vera_acceptance import VeraRuntimeAcceptance


class FakeVera:
    def resume_context(self):
        return {
            "schema": "VERA_MONO_RESUME_CONTEXT_V1",
            "restart_status": "EMPTY",
            "outbound_effect_integrity": {
                "audit_head_digest": "a" * 64,
                "fence_effect_count": 0,
                "audited_effect_count": 0,
            },
        }


ROUTE = CognitionRoute(
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
)


def test_acceptance_binds_request_route_response_and_vera_runtime_context():
    accept = VeraRuntimeAcceptance(FakeVera(), runtime_id="runtime-1")
    request = CognitionRequestEnvelope(
        request_id="r1",
        source="HUMAN",
        reason="desktop",
        task="hello",
        created_at=100.0,
    )

    receipt = accept(request, ROUTE, "answer")

    assert receipt["schema"] == "VERA_RUNTIME_COGNITION_ACCEPTANCE_V1"
    assert receipt["status"] == "ACCEPTED_HOST_OBSERVATION"
    assert receipt["runtime_id"] == "runtime-1"
    assert receipt["request_id"] == "r1"
    assert receipt["route_id"] == "ollama:vera-local:latest"
    assert receipt["canonical_memory_write"] is False
    assert receipt["protected_effect_authority"] is False
    assert len(receipt["context_digest"]) == 64
    assert len(receipt["acceptance_digest"]) == 64


def test_acceptance_digest_changes_when_response_changes():
    accept = VeraRuntimeAcceptance(FakeVera(), runtime_id="runtime-1")
    request = CognitionRequestEnvelope("r1", "HUMAN", "desktop", "hello", 100.0)

    one = accept(request, ROUTE, "answer one")
    two = accept(request, ROUTE, "answer two")

    assert one["acceptance_digest"] != two["acceptance_digest"]
