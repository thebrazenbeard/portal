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


def test_opt_in_context_is_isolated_volatile_and_not_persisted(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    seen = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (LOCAL,),
            invoke_route=lambda route, task: "legacy",
            invoke_chat=lambda route, messages: seen.append(messages) or "reply",
        )
        def run(identifier, task, session=None, source="HUMAN"):
            return engine.process(
                CognitionRequestEnvelope(identifier, source, "test", task, 1),
                conversation_session_id=session,
            )

        assert run("a", "first", "a" * 32).state == "COMPLETED"
        assert seen[-1] == [{"role": "user", "content": "first"}]
        assert run("b", "second", "a" * 32).state == "COMPLETED"
        assert seen[-1] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        assert run("c", "other", "b" * 32).state == "COMPLETED"
        assert seen[-1] == [{"role": "user", "content": "other"}]
        assert run("autonomous", "private autonomy", None, "PRE_ACTIVE_AUTONOMOUS_TURN").state == "COMPLETED"
        assert run("legacy", "legacy no context").state == "COMPLETED"
        assert all("first" not in str(ledger.get(identifier).get("acceptance_json"))
                   for identifier in ("a", "b", "c"))
        assert "first" not in str(ledger.get("b")["task"])
        assert ledger.get("b")["task"] == "second"

        # Cold reopen has no session reconstruction from persisted activities.
        new_engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_chat=lambda route, messages: seen.append(messages) or "new",
        )
        assert new_engine.process(
            CognitionRequestEnvelope("after-restart", "HUMAN", "test", "new", 1),
            conversation_session_id="a" * 32,
        ).state == "COMPLETED"
        assert seen[-1] == [{"role": "user", "content": "new"}]
    finally:
        ledger.close()


def test_request_replay_cannot_cross_conversation_session_or_mode(tmp_path: Path) -> None:
    path = tmp_path / "cognition.sqlite3"
    first = CognitionLedger(path)
    try:
        called = []
        engine = ResidentCognitionEngine(
            ledger=first, discover_routes=lambda: (LOCAL,),
            invoke_chat=lambda _route, messages: called.append(messages) or "private answer",
            invoke_route=lambda _route, task: "plain answer",
        )
        request = CognitionRequestEnvelope("replay-1", "HUMAN", "test", "same task", 1)
        assert engine.process(request, conversation_session_id="a" * 32).response_text == "private answer"
        assert len(called) == 1
        with pytest.raises(ValueError, match="different conversation session"):
            engine.process(request, conversation_session_id="b" * 32)
        with pytest.raises(ValueError, match="different conversation session"):
            engine.process(request)
        # Durable replay protection survives process and ledger reopen.
        row = first.get("replay-1")
        assert row is not None
        assert "a" * 32 not in str(row)
    finally:
        first.close()
    reopened = CognitionLedger(path)
    try:
        engine = ResidentCognitionEngine(
            ledger=reopened, discover_routes=lambda: (LOCAL,),
            invoke_chat=lambda _route, _messages: "must not invoke",
        )
        with pytest.raises(ValueError, match="different conversation session"):
            engine.process(request, conversation_session_id="b" * 32)
        assert engine.process(request, conversation_session_id="a" * 32).response_text == "private answer"
    finally:
        reopened.close()


def test_opt_in_context_bounded_and_never_remembers_failed_turn(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    seen = []
    def invoke(_route, messages):
        seen.append(messages)
        if messages[-1]["content"] == "failure":
            raise RuntimeError("model offline")
        return "reply"
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,), invoke_chat=invoke,
        )
        for i in range(10):
            assert engine.process(
                CognitionRequestEnvelope(f"turn-{i}", "HUMAN", "test", f"turn-{i}", 1),
                conversation_session_id="c" * 32,
            ).state == "COMPLETED"
        assert len(seen[-1]) <= 13  # six preceding user/assistant pairs, plus current
        assert "turn-0" not in str(seen[-1])
        assert engine.process(
            CognitionRequestEnvelope("failure", "HUMAN", "test", "failure", 1),
            conversation_session_id="c" * 32,
        ).state == "FAILED"
        assert engine.process(
            CognitionRequestEnvelope("post-failure", "HUMAN", "test", "next", 1),
            conversation_session_id="c" * 32,
        ).state == "COMPLETED"
        assert "failure" not in str(seen[-1])
        for i in range(40):
            session = f"{i:032x}"
            engine.process(
                CognitionRequestEnvelope(f"session-{i}", "HUMAN", "test", "x", 1),
                conversation_session_id=session,
            )
        assert len(engine._context) == 32
        assert "00000000000000000000000000000000" not in engine._context
        with pytest.raises(ValueError, match="HUMAN"):
            engine.process(
                CognitionRequestEnvelope("bad", "PRE_ACTIVE_AUTONOMOUS_TURN", "test", "no", 1),
                conversation_session_id="c" * 32,
            )
    finally:
        ledger.close()


def test_context_is_pruned_for_json_escape_amplification(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    captured = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_chat=lambda route, messages: captured.append(messages) or ("\\" * 5000),
        )
        sid = "d" * 32
        engine.process(CognitionRequestEnvelope("one", "HUMAN", "test", "first", 1),
                       conversation_session_id=sid)
        # A 5KiB answer is within the memory budget, but its JSON encoding
        # doubles because of escaping. Do not issue an oversized model body.
        engine.process(CognitionRequestEnvelope("two", "HUMAN", "test", "x" * 14000, 1),
                       conversation_session_id=sid)
        assert captured[-1] == [{"role": "user", "content": "x" * 14000}]
    finally:
        ledger.close()
