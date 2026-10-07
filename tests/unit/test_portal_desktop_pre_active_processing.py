from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from portal.desktop_pre_active import process_one_autonomous_turn
from portal.desktop_runtime import CognitionResult


@dataclass
class Store:
    connection: sqlite3.Connection
    acked: list[str]
    failed: list[dict[str, object]]
    journal: list[dict[str, object]]

    def append_journal(self, *, event_type, subject_id, payload, now):
        self.journal.append(
            {
                "event_type": event_type,
                "subject_id": subject_id,
                "payload": payload,
                "now": now,
            }
        )

    def ack_event(self, event_id, *, worker_id, lease_token, now):
        self.acked.append(event_id)
        self.connection.execute(
            "UPDATE events SET status='DONE' WHERE id=?",
            (event_id,),
        )

    def fail_event(
        self,
        event_id,
        *,
        worker_id,
        lease_token,
        now,
        retry_at=None,
        max_attempts=None,
        error=None,
        failed_run_id=None,
    ):
        self.failed.append(
            {
                "event_id": event_id,
                "retry_at": retry_at,
                "max_attempts": max_attempts,
                "error": error,
            }
        )
        self.connection.execute(
            "UPDATE events SET status='PENDING' WHERE id=?",
            (event_id,),
        )
        return False


def make_store() -> Store:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE events(
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            available_at REAL NOT NULL,
            created_at REAL NOT NULL,
            lease_owner TEXT,
            lease_until REAL,
            lease_token TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    c.execute(
        """
        INSERT INTO events(
            id,kind,payload_json,priority,status,attempts,
            available_at,created_at,updated_at
        ) VALUES(?,?,?,?,?,0,0,0,0)
        """,
        (
            "turn-1",
            "autonomous.turn",
            json.dumps(
                {
                    "task": "think",
                    "capabilities": ["text"],
                    "source": "ENDOGENOUS",
                    "reason": "open loop",
                }
            ),
            1,
            "PENDING",
        ),
    )
    return Store(c, [], [], [])


class Engine:
    def __init__(self, result: CognitionResult):
        self.result = result
        self.requests = []

    def process(self, request, *, now=None):
        self.requests.append(request)
        return self.result


def test_completed_autonomous_cognition_is_acknowledged() -> None:
    store = make_store()
    engine = Engine(
        CognitionResult(
            request_id="turn-1",
            state="COMPLETED",
            route_id="ollama:vera-local:latest",
            response_text="ok",
            retryable=False,
            evidence_id="cognition:ok",
        )
    )

    outcome = process_one_autonomous_turn(
        store,
        engine,
        now=10.0,
        retry_delay_seconds=60.0,
        max_attempts=5,
    )

    assert outcome is not None
    assert outcome.state == "COMPLETED"
    assert store.acked == ["turn-1"]
    assert store.failed == []
    assert engine.requests[0].source == "PRE_ACTIVE_AUTONOMOUS_TURN"
    assert engine.requests[0].task == "think"


def test_failed_or_unresolved_autonomous_cognition_is_retry_scheduled() -> None:
    for state in ("FAILED", "UNRESOLVED"):
        store = make_store()
        engine = Engine(
            CognitionResult(
                request_id="turn-1",
                state=state,
                route_id=None,
                response_text=None,
                retryable=True,
                evidence_id="cognition:nope",
                error="no route",
            )
        )

        outcome = process_one_autonomous_turn(
            store,
            engine,
            now=10.0,
            retry_delay_seconds=60.0,
            max_attempts=5,
        )

        assert outcome is not None
        assert store.acked == []
        assert len(store.failed) == 1
        assert store.failed[0]["retry_at"] == 70.0
        assert store.failed[0]["max_attempts"] == 5
