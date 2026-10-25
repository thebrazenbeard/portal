from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
import pytest
import portal.desktop_runtime as desktop_runtime

from portal.desktop_cognition import CognitionRoute, discover_cognition_routes
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


def test_rejected_intake_does_not_automatically_repeat_ambiguous_acceptance(tmp_path):
    ledger = CognitionLedger(tmp_path / "ledger.db")
    calls = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda route, task: "answer",
            accept_result=lambda *args: calls.append("accept") or (_ for _ in ()).throw(ValueError("intake rejected")),
        )
        request = CognitionRequestEnvelope("rejected", "HUMAN", "test", "hi", 100)
        result = engine.process(request)
        assert result.state == "FAILED"
        assert not result.retryable
        assert "intake rejected" in result.error
        assert engine.process(request).state == "FAILED"
        assert calls == ["accept"]
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


def test_local_text_discovery_does_not_authorize_gateway_learning_or_rezon_collective(
    tmp_path: Path,
) -> None:
    """A raw model endpoint is not an attested learner or collective adapter."""
    routes = discover_cognition_routes(
        command_probe=lambda _name, _args: None,
        ollama_tags=lambda: {"models": [{"name": "vera-local:latest", "digest": "a" * 64}]},
        pre_active_models=lambda: {
            "data": [{"id": "qwen-local", "adapter_active": False,
                      "effect_authority": False}],
        },
    )
    assert {route.provider for route in routes} == {"ollama", "pre_active_local"}
    called: list[str] = []
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: routes,
            invoke_route=lambda _route, task: called.append(task) or "raw text",
        )
        for index, capability in enumerate((
            "gateway_authorized_inference", "rezon_collective_advisory",
        )):
            request = CognitionRequestEnvelope(
                request_id=f"protected-{index}",
                source="PRE_ACTIVE_AUTONOMOUS_TURN",
                reason="requires a governed source",
                task=f"protected task {index}",
                created_at=100.0,
                required_capabilities=(capability,),
            )
            result = engine.process(request, now=101.0)
            assert result.state == "UNRESOLVED"
            assert result.route_id is None
            assert result.retryable is True
            stored = ledger.get(request.request_id)
            assert stored is not None
            assert stored["state"] == "UNRESOLVED"
            assert stored["protected_effect_authority"] == 0
        assert called == []  # Neither protected request reached raw Ollama.

        # Ordinary local, tool-free cognition continues to work.
        ordinary = engine.process(
            CognitionRequestEnvelope("human-text", "HUMAN", "chat", "hello", 100.0),
            now=102.0,
        )
        assert ordinary.state == "COMPLETED"
        assert ordinary.response_text == "raw text"
        assert called == ["hello"]
        assert ledger.get("human-text")["protected_effect_authority"] == 0
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


def test_completed_request_replay_requires_identical_capability_identity(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "cognition.sqlite3")
    calls = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda _route, task: calls.append(task) or "first",
        )
        request = CognitionRequestEnvelope("cap-replay", "HUMAN", "test", "one", 100.0)
        assert engine.process(request).state == "COMPLETED"
        with pytest.raises(ValueError, match="different cognition request"):
            engine.process(CognitionRequestEnvelope(
                "cap-replay", "HUMAN", "test", "one", 100.0,
                required_capabilities=("text", "tools"),
            ))
        with pytest.raises(ValueError, match="different cognition request"):
            engine.process(CognitionRequestEnvelope(
                "cap-replay", "HUMAN", "test", "one", 101.0,
            ))
        assert calls == ["one"]
    finally:
        ledger.close()


def test_concurrent_connections_claim_one_cognition_request(tmp_path: Path) -> None:
    path = tmp_path / "cognition.sqlite3"
    entering = Barrier(2)
    release = Barrier(2)
    invocations = []
    accepted = []

    def invoke(_route, _task):
        invocations.append("invoke")
        entering.wait(timeout=5)
        release.wait(timeout=5)
        return "answer"

    def run_first():
        ledger = CognitionLedger(path)
        try:
            engine = ResidentCognitionEngine(
                ledger=ledger, discover_routes=lambda: (LOCAL,),
                invoke_route=invoke,
                accept_result=lambda *_: accepted.append("accept") or {"status": "ACCEPTED"},
            )
            return engine.process(CognitionRequestEnvelope("shared", "HUMAN", "test", "ask", 100.0))
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run_first)
        entering.wait(timeout=5)
        competing_ledger = CognitionLedger(path)
        try:
            competing_engine = ResidentCognitionEngine(
                ledger=competing_ledger, discover_routes=lambda: (LOCAL,),
                invoke_route=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate invocation")),
                accept_result=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate acceptance")),
            )
            other = competing_engine.process(CognitionRequestEnvelope("shared", "HUMAN", "test", "ask", 100.0))
            assert other.state == "RUNNING"
            assert other.retryable
            assert other.response_text is None
            assert invocations == ["invoke"]
        finally:
            competing_ledger.close()
            release.wait(timeout=5)
        assert first.result(timeout=5).state == "COMPLETED"
    assert accepted == ["accept"]


def test_unaccepted_crash_requires_explicit_reconciliation_before_retry(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    request = CognitionRequestEnvelope("crash", "HUMAN", "test", "ask", 100.0)
    ledger = CognitionLedger(path)
    try:
        original = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("host died")),
        )
        with pytest.raises(KeyboardInterrupt):
            original.process(request)
        row = ledger.get("crash")
        assert row["state"] == "RUNNING" and row["attempt_token"]
    finally:
        ledger.close()
    reopened = CognitionLedger(path)
    try:
        calls = []
        engine = ResidentCognitionEngine(
            ledger=reopened, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda *_: calls.append("invoke") or "good",
        )
        assert engine.process(request).state == "RUNNING"
        assert calls == []
        with pytest.raises(ValueError, match="explicit owner confirmation"):
            reopened.reconcile_abandoned("crash", row["attempt_token"], confirmed_owner_stopped=False)
        assert reopened.reconcile_abandoned(
            "crash", row["attempt_token"], confirmed_owner_stopped=True,
        )
        assert engine.process(request).state == "COMPLETED"
        assert calls == ["invoke"]
    finally:
        reopened.close()


def test_stale_owner_cannot_accept_after_explicit_reconciliation(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    first_invoke = Barrier(2)
    release_first = Barrier(2)
    accepted = []
    request = CognitionRequestEnvelope("stale", "HUMAN", "test", "ask", 100.0)

    def run_stale():
        ledger = CognitionLedger(path)
        try:
            def invoke(*_):
                first_invoke.wait(timeout=5)
                release_first.wait(timeout=5)
                return "stale reply"
            return ResidentCognitionEngine(
                ledger=ledger, discover_routes=lambda: (LOCAL,),
                invoke_route=invoke,
                accept_result=lambda *_: accepted.append("stale") or {},
            ).process(request)
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        previous = pool.submit(run_stale)
        first_invoke.wait(timeout=5)
        new_ledger = CognitionLedger(path)
        try:
            token = new_ledger.get("stale")["attempt_token"]
            # This is an explicit operator override, not automatic time expiry.
            assert new_ledger.reconcile_abandoned(
                "stale", token, confirmed_owner_stopped=True,
            )
            replacement = ResidentCognitionEngine(
                ledger=new_ledger, discover_routes=lambda: (LOCAL,),
                invoke_route=lambda *_: "new reply",
                accept_result=lambda *_: accepted.append("new") or {},
            )
            assert replacement.process(request).state == "COMPLETED"
        finally:
            release_first.wait(timeout=5)
            new_ledger.close()
        with pytest.raises(RuntimeError, match="no longer current"):
            previous.result(timeout=5)
    assert accepted == ["new"]


def test_legacy_row_without_capability_identity_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    ledger = CognitionLedger(path)
    request = CognitionRequestEnvelope("legacy", "HUMAN", "test", "ask", 100.0)
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,), invoke_route=lambda *_: "first",
        )
        assert engine.process(request).state == "COMPLETED"
        ledger.connection.execute(
            "UPDATE desktop_cognition_runs SET required_capabilities_json=NULL WHERE request_id='legacy'"
        )
        with pytest.raises(ValueError, match="different cognition request"):
            engine.process(request)
    finally:
        ledger.close()


def test_discovery_exception_is_terminal_retryable_not_stuck_running(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "ledger.db")
    request = CognitionRequestEnvelope("discovery", "HUMAN", "test", "ask", 100.0)
    try:
        broken = ResidentCognitionEngine(
            ledger=ledger,
            discover_routes=lambda: (_ for _ in ()).throw(RuntimeError("discovery offline")),
        )
        result = broken.process(request)
        assert result.state == "FAILED" and result.retryable
        assert "discovery offline" in result.error
        recovered = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,), invoke_route=lambda *_: "ok",
        )
        assert recovered.process(request).state == "COMPLETED"
    finally:
        ledger.close()


def test_unknown_acceptance_outcome_cannot_be_reset_for_automatic_replay(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    ledger = CognitionLedger(path)
    calls = []
    request = CognitionRequestEnvelope("unknown", "HUMAN", "test", "ask", 100.0)
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda *_: "answer",
            accept_result=lambda *_: calls.append("accept") or (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            engine.process(request)
        row = ledger.get("unknown")
        assert row["state"] == "RUNNING" and row["acceptance_claimed"] == 1
        assert row["retryable"] == 0
        assert engine.process(request).error == "acceptance_outcome_unknown"
        with pytest.raises(ValueError, match="acceptance outcome unknown"):
            ledger.reconcile_abandoned(
                "unknown", row["attempt_token"], confirmed_owner_stopped=True,
            )
        assert calls == ["accept"]
    finally:
        ledger.close()


def test_capability_identity_is_set_order_invariant_but_rejects_invalid_unicode(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "ledger.db")
    broad = CognitionRoute(
        route_id="broad", provider="ollama", model_or_agent="vera-local:latest",
        local=True, available=True, current=True,
        capabilities=("text", "summary"), incremental_paid_compute=False,
        auto_admissible=True, effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
        preference=10,
    )
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (broad,), invoke_route=lambda *_: "answer",
        )
        first = CognitionRequestEnvelope("ordered", "HUMAN", "test", "ask", 100.0, ("summary", "text"))
        replay = CognitionRequestEnvelope("ordered", "HUMAN", "test", "ask", 100.0, ("text", "summary"))
        assert engine.process(first).state == "COMPLETED"
        assert engine.process(replay).state == "COMPLETED"
        with pytest.raises(ValueError, match="invalid cognition capability"):
            engine.process(CognitionRequestEnvelope(
                "invalid", "HUMAN", "test", "ask", 100.0, ("text", "\ud800"),
            ))
        assert ledger.get("invalid") is None
    finally:
        ledger.close()


def test_invalid_utf8_model_reply_is_failed_before_acceptance_callback(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "ledger.db")
    accepted = []
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda *_: "claim\ud800",
            accept_result=lambda *_: accepted.append("accepted") or {},
        )
        result = engine.process(CognitionRequestEnvelope("invalid-reply", "HUMAN", "test", "ask", 100.0))
        assert result.state == "FAILED" and result.retryable
        assert accepted == []
        assert ledger.get("invalid-reply")["acceptance_claimed"] == 0
    finally:
        ledger.close()


def test_nonserializable_acceptance_receipt_is_terminal_and_not_reinvoked(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "ledger.db")
    accepted = []
    request = CognitionRequestEnvelope("bad-receipt", "HUMAN", "test", "ask", 100.0)
    try:
        engine = ResidentCognitionEngine(
            ledger=ledger, discover_routes=lambda: (LOCAL,),
            invoke_route=lambda *_: "reply",
            accept_result=lambda *_: accepted.append("accepted") or {"receipt": object()},
        )
        result = engine.process(request)
        assert result.state == "FAILED" and not result.retryable
        assert engine.process(request).state == "FAILED"
        assert accepted == ["accepted"]
    finally:
        ledger.close()


def test_direct_ledger_finish_cannot_make_reserved_acceptance_retryable(tmp_path: Path) -> None:
    ledger = CognitionLedger(tmp_path / "ledger.db")
    request = CognitionRequestEnvelope("guard", "HUMAN", "test", "ask", 100.0)
    try:
        token, old = ledger.claim(request, now=101.0, conversation_binding_digest=None)
        assert token is not None and old is None
        ledger.begin_acceptance(request.request_id, token)
        with pytest.raises(ValueError, match="reserved acceptance cannot be retryable"):
            ledger.finish(
                request, attempt_token=token, state="FAILED", retryable=True,
                now=102.0, route=LOCAL, error="claimed callback failed",
                conversation_binding_digest=None, acceptance_claimed=True,
            )
        row = ledger.get(request.request_id)
        assert row["state"] == "RUNNING" and row["retryable"] == 0
        same_attempt, held = ledger.claim(request, now=103.0, conversation_binding_digest=None)
        assert same_attempt is None and held["acceptance_claimed"] == 1
    finally:
        ledger.close()


def test_multiple_cold_hosts_migrate_legacy_ledger_without_duplicate_alter(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE desktop_cognition_runs(request_id TEXT PRIMARY KEY, state TEXT)")

    opening = Barrier(6)

    def open_and_check_columns(_index):
        opening.wait(timeout=5)
        ledger = CognitionLedger(path)
        try:
            return {row[1] for row in ledger.connection.execute(
                "PRAGMA table_info(desktop_cognition_runs)"
            )}
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(open_and_check_columns, range(6)))
    for columns in results:
        assert {"acceptance_json", "conversation_binding_digest", "required_capabilities_json",
                "attempt_token", "acceptance_claimed"}.issubset(columns)


def test_cold_start_retries_only_transient_wal_journal_lock(tmp_path: Path, monkeypatch) -> None:
    original_connect = sqlite3.connect
    attempts = []

    class TransientLockConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL":
                attempts.append(sql)
                if len(attempts) < 3:
                    raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        desktop_runtime.sqlite3, "connect",
        lambda *args, **kwargs: original_connect(*args, factory=TransientLockConnection, **kwargs),
    )
    ledger = CognitionLedger(tmp_path / "ledger.db")
    try:
        assert len(attempts) == 3
        assert ledger.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        ledger.close()


def test_cold_start_does_not_mask_nonlock_wal_failure(tmp_path: Path, monkeypatch) -> None:
    original_connect = sqlite3.connect
    attempts = []

    class FailedWalConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL":
                attempts.append(sql)
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        desktop_runtime.sqlite3, "connect",
        lambda *args, **kwargs: original_connect(*args, factory=FailedWalConnection, **kwargs),
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        CognitionLedger(tmp_path / "ledger.db")
    assert len(attempts) == 1


@pytest.mark.parametrize("extended_busy_code", [517, 262])
def test_cold_start_retries_extended_sqlite_busy_lock_codes(
    tmp_path: Path, monkeypatch, extended_busy_code: int,
) -> None:
    original_connect = sqlite3.connect
    attempts = []

    class ExtendedBusyConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL":
                attempts.append(sql)
                if len(attempts) < 3:
                    exc = sqlite3.OperationalError("database is locked")
                    exc.sqlite_errorcode = extended_busy_code
                    raise exc
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        desktop_runtime.sqlite3, "connect",
        lambda *args, **kwargs: original_connect(*args, factory=ExtendedBusyConnection, **kwargs),
    )
    ledger = CognitionLedger(tmp_path / "ledger.db")
    try:
        assert len(attempts) == 3
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
