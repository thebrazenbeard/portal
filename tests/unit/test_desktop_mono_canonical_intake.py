"""A1 RED packet: real Desktop handler/engine and Mono, disposable state only.

Run with the exact Mono #77 packages/*/src on PYTHONPATH. These requirements
were RED at PORTAL #52. Original source/logs remain in the A1 evidence packet.
Owner contract #6233757408 and Integration ACK #6234499327 require explicit
fixture locators and HELD before discovery/operational writes. The five case
names and their source/provenance assertions remain; their setup/operational
expectations now implement that policy rather than the pre-contract behavior.
Only discovery and model transport are replaced with offline test boundaries.
"""
from __future__ import annotations

import json

import pytest

from portal.desktop_app import DesktopViewModel
from portal.desktop_cognition import CognitionRoute
from portal.desktop_host import prepare_runtime_layout
from portal.desktop_ipc import DesktopRuntimeCommandHandler
from portal.desktop_runtime import CognitionLedger, ResidentCognitionEngine
from portal.desktop_vera_acceptance import VeraRuntimeAcceptance
pytest.importorskip("vera_core", reason="requires source-bound Mono #77; see docs/DESKTOP_CANONICAL_INTAKE_V1.md")
from vera_core import QualifiedVeraRuntime, VeraStateDirectory


@pytest.fixture
def desktop(tmp_path):
    layout = prepare_runtime_layout(tmp_path / "desktop")
    state = VeraStateDirectory(
        layout["state_root"] / "vera",
        project_id="VERA_MONO",
        identity_id="VERA_PROJECT_IDENTITY_V1",
    )
    runtime = QualifiedVeraRuntime.from_state_directory(state)
    ledger = CognitionLedger(
        layout["state_root"] / "cognition" / "desktop-cognition.sqlite3"
    )
    memory_head = runtime.lifecycle.memory.current_head
    try:
        yield runtime, ledger
    finally:
        ledger.close()
        assert runtime.lifecycle.memory.current_head == memory_head
        assert runtime.resume_context()["outbound_effect_integrity"]["fence_effect_count"] == 0


def handler_for(desktop, *, discover_routes, invoke_route=None):
    runtime, ledger = desktop

    def forbidden_transport(_route, _text):
        raise AssertionError("no provider/model/remote call is authorized")

    engine = ResidentCognitionEngine(
        ledger=ledger,
        discover_routes=discover_routes,
        invoke_route=invoke_route or forbidden_transport,
        accept_result=VeraRuntimeAcceptance(runtime, runtime_id="offline-red-fixture"),
    )
    return DesktopRuntimeCommandHandler(
        engine=engine,
        discover_routes=discover_routes,
        runtime_status=lambda: {"state": "SOURCE_TEST_ONLY"},
        canonical_runtime=runtime,
    )


def payload(text="Literal user input"):
    return {
        "command": "desktop_cognize",
        "request_id": "fixture-message-1",
        # A test locator, not a personal identity or a proposed UI default.
        "conversation_id": "fixture-conversation",
        "source": "HUMAN",
        "reason": "offline canonical-intake RED",
        "task": text,
    }


class HandlerClient:
    """Use the actual command handler behind the actual view model."""

    def __init__(self, handler):
        self.handler = handler
        self.sent = []

    def request(self, command, **body):
        request = {"command": command, **body}
        self.sent.append(request)
        return self.handler.handle(request)


def test_canonical_task_is_opened_before_desktop_route_discovery(desktop):
    runtime, ledger = desktop
    events_at_discovery = []

    def discover():
        events_at_discovery.append(runtime.tasks.events())
        return ()

    handler = handler_for(desktop, discover_routes=discover)
    result = handler.handle(payload())
    assert result["state"] == "HELD"
    assert ledger.get("fixture-message-1") is None
    assert events_at_discovery == []  # No qualified provider gate: no discovery.
    opened = [e for e in runtime.tasks.events() if e.event_type == "TASK_OPENED"]
    assert len(opened) == 1, "Desktop reached route discovery with zero canonical TASK_OPENED"


@pytest.mark.parametrize("entrypoint", ["handler", "view_model"])
def test_literal_text_is_preserved_or_explicitly_rejected(desktop, entrypoint):
    _runtime, ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: ())
    original = " \tLiteral Unicode input: \u03bb\n"
    try:
        if entrypoint == "handler":
            handler.handle(payload(original))
        else:
            DesktopViewModel(HandlerClient(handler), conversation_id="fixture-conversation").send_message(
                original, request_id="fixture-message-1", created_at=123.0
            )
    except ValueError:
        # Mono #77 rejects surrounding whitespace. Explicit rejection is safe.
        assert ledger.get("fixture-message-1") is None
        return
    row = ledger.get("fixture-message-1")
    assert row is not None
    assert row["task"] == original, "Desktop silently replaced original source bytes"


def test_operational_success_cannot_stand_in_for_canonical_completion(desktop):
    runtime, ledger = desktop
    synthetic_calls = []
    route = CognitionRoute(
        route_id="offline:synthetic-fixture", provider="offline-fixture",
        model_or_agent="NO_MODEL", local=True, available=True, current=True,
        capabilities=("text",), incremental_paid_compute=False,
        auto_admissible=True,
        effect_authority_ceiling="COGNITION_ONLY_NO_PROTECTED_EFFECT",
    )
    def synthetic_transport(_route, text):
        synthetic_calls.append(text)
        return "SYNTHETIC FIXTURE; not an authenticated result"

    handler = handler_for(
        desktop, discover_routes=lambda: (route,),
        invoke_route=synthetic_transport,
    )
    result = handler.handle(payload())
    row = ledger.get("fixture-message-1")
    receipt = json.loads(row["acceptance_json"]) if row and row["acceptance_json"] else None
    if receipt is not None:
        assert receipt["canonical_memory_write"] is False
    opened = [e for e in runtime.tasks.events() if e.event_type == "TASK_OPENED"]
    print(json.dumps({
        "operational_state": row["state"] if row else None,
        "acceptance": receipt["status"] if receipt else None,
        "canonical_task_opened_count": len(opened),
        "synthetic_transport_calls": len(synthetic_calls),
    }))
    assert result["protected_effect_authority"] is False
    assert len(opened) == 1, (
        "An operational success receipt does not supply the required canonical task"
    )
    assert not runtime.tasks.read(opened[0].task_id).closed
    assert synthetic_calls == [], "an offline advertisement is not provider admission"


def test_actual_view_model_supplies_conversation_locator(desktop):
    handler = handler_for(desktop, discover_routes=lambda: ())
    client = HandlerClient(handler)
    DesktopViewModel(client, conversation_id="fixture-conversation").send_message(
        "Literal user input", request_id="fixture-message-1", created_at=123.0
    )
    assert client.sent[0].get("conversation_id"), (
        "Owner decision required: Desktop has no conversation locator contract"
    )


@pytest.mark.parametrize("key,value", [
    ("conversation_id", None), ("conversation_id", ""), ("conversation_id", " leading"),
    ("conversation_id", "bad!"), ("conversation_id", "x" * 97),
    ("request_id", None), ("request_id", ""), ("request_id", ".legacy"),
    ("request_id", "x" * 97), ("request_id", 123),
])
def test_invalid_locators_block_before_any_task_or_operational_write(desktop, key, value):
    runtime, ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    with pytest.raises(ValueError, match="REQUIRED.*BLOCKED"):
        handler.handle({**payload(), key: value})
    assert runtime.tasks.events() == ()
    assert ledger.list_recent() == []


def test_canonical_source_replay_is_exact_and_substitution_fails(desktop):
    runtime, ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    first = handler.handle(payload("Unicode \u03bb with\ninternal whitespace"))
    replay = handler.handle(payload("Unicode \u03bb with\ninternal whitespace"))
    assert replay == first
    assert first["message_id"] == "fixture-message-1"
    assert first["status"] == "PENDING_NO_ADMITTED_PROVIDER"
    assert len(runtime.tasks.events()) == 1
    with pytest.raises(ValueError):
        handler.handle(payload("changed original"))
    assert len(runtime.tasks.events()) == 1
    assert runtime.tasks.read(first["task_id"]).packet.subject == first["text"]
    # A new request_id is explicitly a distinct message within this locator.
    another = handler.handle({**payload("new message"), "request_id": "fixture-message-2"})
    assert another["task_id"] != first["task_id"]
    assert ledger.list_recent() == []


@pytest.mark.parametrize("operational_state", ["RUNNING", "FAILED", "COMPLETED"])
def test_legacy_operational_records_cannot_supply_canonical_results(desktop, operational_state):
    from portal.desktop_runtime import CognitionRequestEnvelope

    runtime, ledger = desktop
    ledger.store(CognitionRequestEnvelope("fixture-message-1", "HUMAN", "legacy", "legacy bytes", 1),
                 state=operational_state, retryable=True, now=1, response_text="fabricated old success")
    before = ledger.get("fixture-message-1")
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    result = handler.handle(payload())
    assert result["state"] == "HELD"
    assert result["accepted_as_task_result"] is False
    assert result["response_text"] is None
    assert ledger.get("fixture-message-1") == before
    assert len(runtime.tasks.events()) == 1
    assert runtime.tasks.read(result["task_id"]).packet.subject == "Literal user input"


def test_stale_task_head_cannot_be_reset_or_retried_through_desktop(desktop):
    runtime, _ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    opened = handler.handle(payload())
    values = dict(completed_evidence=(), blockers=("owner review",),
                  protected_effects_still_gated=("COGNITION_TURN",), next_frontier="review",
                  lifecycle_evidence_digest="a" * 64, expected_task_head=opened["opened_head"])
    runtime.tasks.checkpoint(opened["task_id"], "advance", **values)
    with pytest.raises(ValueError):
        runtime.tasks.checkpoint(opened["task_id"], "stale", **values)
    result = handler.handle(payload())
    assert result["state"] == "HELD"
    assert result["status"] == "HELD_TASK_CHANGED"
    assert result["task_journal_head"] != opened["opened_head"]
    assert len(runtime.tasks.events()) == 2


def test_recovered_claim_remains_unknown_without_any_dispatch(desktop):
    runtime, ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    first = handler.handle(payload(), outcome_unknown=True)
    assert first["status"] == "OUTCOME_UNKNOWN_NO_REDISPATCH"
    assert len(runtime.tasks.events()) == 1
    assert ledger.list_recent() == []


def test_history_reads_source_without_opening_another_task(desktop):
    runtime, _ledger = desktop
    handler = handler_for(desktop, discover_routes=lambda: pytest.fail("must not discover"))
    first = handler.handle(payload())
    head = runtime.tasks.head
    rows = handler.handle({"command": "desktop_conversation_history", "conversation_id": "fixture-conversation"})["items"]
    assert len(rows) == 1
    assert rows[0]["task_id"] == first["task_id"]
    assert rows[0]["source_digest"] == first["source_digest"]
    assert rows[0]["memory_admitted"] is False
    assert rows[0]["status"] == "PENDING_NO_ADMITTED_PROVIDER"
    assert runtime.tasks.head == head


def test_cold_process_reopens_same_task_source_and_hold(desktop):
    import os
    import subprocess
    import sys
    import textwrap

    runtime, _ledger = desktop
    first = handler_for(desktop, discover_routes=lambda: ()).handle(payload())
    memory_head = runtime.lifecycle.memory.current_head
    program = textwrap.dedent('''
        import json,sys
        from pathlib import Path
        from vera_core import QualifiedVeraRuntime,VeraStateDirectory
        from portal.desktop_ipc import DesktopRuntimeCommandHandler
        from portal.desktop_runtime import CognitionLedger,ResidentCognitionEngine
        r=QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(sys.argv[1],project_id="VERA_MONO",identity_id="VERA_PROJECT_IDENTITY_V1"))
        l=CognitionLedger(Path(sys.argv[1]).parent/"child-operational.sqlite")
        def forbidden(*args): raise AssertionError("must not dispatch")
        h=DesktopRuntimeCommandHandler(engine=ResidentCognitionEngine(ledger=l,discover_routes=forbidden,invoke_route=forbidden),discover_routes=forbidden,runtime_status=lambda:{},canonical_runtime=r)
        try:
            result=h.handle(json.loads(sys.argv[2]))
            history=h.handle({"command":"desktop_conversation_history","conversation_id":"fixture-conversation"})
            print(json.dumps({"result":result,"history":history,"count":len(r.tasks.events()),"memory_head":r.lifecycle.memory.current_head,"operational_count":len(l.list_recent())}))
        finally: l.close()
    ''')
    child = subprocess.run([sys.executable, "-B", "-c", program,
                            str(runtime.tasks.path.parent.parent), json.dumps(payload())],
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                           text=True, capture_output=True, timeout=30)
    assert child.returncode == 0, child.stderr
    found = json.loads(child.stdout)
    assert found["count"] == 1
    assert found["operational_count"] == 0
    assert found["memory_head"] == memory_head
    assert found["result"] == first
    assert found["history"]["items"][0]["source_digest"] == first["source_digest"]
    assert found["history"]["items"][0]["memory_admitted"] is False
