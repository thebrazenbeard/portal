from __future__ import annotations

from pathlib import Path
import pytest

from portal.desktop_cognition import CognitionRoute
from portal.desktop_runtime import (
    CognitionLedger,
    CognitionRequestEnvelope,
    ResidentCognitionEngine,
)


LOCAL = CognitionRoute(
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
)


def test_acceptance_precedes_completion_and_persists_receipt(tmp_path):
    ledger = CognitionLedger(tmp_path / "ledger.db")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda route, task: "observed answer",
            accept_result=lambda request, route, text: {"status": "ACCEPTED_OBSERVATION", "receipt": "intake-1"},
        )
        request = CognitionRequestEnvelope("accepted", "HUMAN", "test", "hello", 100)
        assert engine.process(request).state == "COMPLETED"
        assert 'intake-1' in ledger.get("accepted")["acceptance_json"]
        with pytest.raises(ValueError, match="different"):
            engine.process(CognitionRequestEnvelope("accepted", "HUMAN", "test", "changed", 100))
    finally:
        ledger.close()


def test_rejected_intake_keeps_request_retryable(tmp_path):
    ledger = CognitionLedger(tmp_path / "ledger.db")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda route, task: "answer",
            accept_result=lambda *args: (_ for _ in ()).throw(ValueError("intake rejected")),
        )
        result = engine.process(CognitionRequestEnvelope("rejected", "HUMAN", "test", "hi", 100))
        assert result.state == "FAILED"
        assert result.retryable
        assert "intake rejected" in result.error
    finally:
        ledger.close()


def test_human_request_uses_selected_route_and_persists_provenance(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda route, task: f"{route.route_id}:{task}",
        )
        request = CognitionRequestEnvelope(
            request_id="human-1",
            source="HUMAN",
            reason="Patrick sent a message",
            task="hello",
            created_at=100.0,
        )

        result = engine.process(request, now=101.0)

        assert result.state == "COMPLETED"
        assert result.route_id == "ollama:vera-local:latest"
        assert result.response_text == "ollama:vera-local:latest:hello"
        stored = ledger.get("human-1")
        assert stored is not None
        assert stored["state"] == "COMPLETED"
        assert stored["provider"] == "ollama"
        assert stored["model_or_agent"] == "vera-local:latest"
        assert stored["protected_effect_authority"] == 0
    finally:
        ledger.close()


def test_pre_active_autonomous_request_uses_same_non_chatgpt_cognition_path(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    calls: list[str] = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, task: calls.append(task) or "autonomous result",
        )
        request = CognitionRequestEnvelope(
            request_id="autonomous-event-7",
            source="PRE_ACTIVE_AUTONOMOUS_TURN",
            reason="open loop became actionable",
            task="inspect the open loop",
            created_at=100.0,
        )

        result = engine.process(request, now=101.0)

        assert result.state == "COMPLETED"
        assert result.response_text == "autonomous result"
        assert calls == ["inspect the open loop"]
        assert ledger.get("autonomous-event-7")["source"] == "PRE_ACTIVE_AUTONOMOUS_TURN"
    finally:
        ledger.close()


def test_no_admissible_route_is_unresolved_and_retryable(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (),
            invoke_route=lambda _route, _task: (_ for _ in ()).throw(
                AssertionError("must not invoke")
            ),
        )
        request = CognitionRequestEnvelope(
            request_id="autonomous-event-8",
            source="PRE_ACTIVE_AUTONOMOUS_TURN",
            reason="temporal trigger",
            task="think",
            created_at=100.0,
        )

        result = engine.process(request, now=101.0)

        assert result.state == "UNRESOLVED"
        assert result.route_id is None
        assert result.retryable is True
        stored = ledger.get("autonomous-event-8")
        assert stored["state"] == "UNRESOLVED"
        assert stored["retryable"] == 1
    finally:
        ledger.close()


def test_completed_request_is_idempotent_and_not_reinvoked(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    calls: list[str] = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, task: calls.append(task) or "first",
        )
        request = CognitionRequestEnvelope(
            request_id="same",
            source="HUMAN",
            reason="test",
            task="one",
            created_at=100.0,
        )
        first = engine.process(request, now=101.0)
        second = engine.process(request, now=102.0)

        assert first.state == second.state == "COMPLETED"
        assert first.response_text == second.response_text == "first"
        assert calls == ["one"]
    finally:
        ledger.close()


def test_route_failure_is_retryable_and_does_not_gain_effect_authority(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, _task: (_ for _ in ()).throw(
                RuntimeError("model offline")
            ),
        )
        request = CognitionRequestEnvelope(
            request_id="failure-1",
            source="PRE_ACTIVE_AUTONOMOUS_TURN",
            reason="test",
            task="one",
            created_at=100.0,
        )

        result = engine.process(request, now=101.0)

        assert result.state == "FAILED"
        assert result.retryable is True
        stored = ledger.get("failure-1")
        assert stored["protected_effect_authority"] == 0
        assert "model offline" in stored["error"]
    finally:
        ledger.close()
