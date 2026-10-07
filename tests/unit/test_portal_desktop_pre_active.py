from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from portal.desktop_pre_active import (
    claim_ready_event_kind,
    process_one_autonomous_turn,
)
from portal.desktop_runtime import CognitionResult


@dataclass
class FakeStore:
    connection: sqlite3.Connection
    journal: list[tuple[str, str, dict[str, object], float]]
    acks: list[tuple[str, str, str, float]] | None = None
    failures: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.acks is None:
            self.acks = []
        if self.failures is None:
            self.failures = []

    def append_journal(
        self,
        *,
        event_type: str,
        subject_id: str,
        payload: dict[str, object],
        now: float,
    ) -> None:
        self.journal.append((event_type, subject_id, payload, now))

    def ack_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: float,
    ) -> None:
        assert self.acks is not None
        self.acks.append((event_id, worker_id, lease_token, now))
        self.connection.execute(
            "UPDATE events SET status='DONE' WHERE id=?",
            (event_id,),
        )

    def fail_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: float,
        retry_at: float | None = None,
        max_attempts: int | None = None,
        error: str | None = None,
        failed_run_id: str | None = None,
    ) -> bool:
        assert self.failures is not None
        self.failures.append(
            {
                "event_id": event_id,
                "worker_id": worker_id,
                "lease_token": lease_token,
                "now": now,
                "retry_at": retry_at,
                "max_attempts": max_attempts,
                "error": error,
                "failed_run_id": failed_run_id,
            }
        )
        self.connection.execute(
            "UPDATE events SET status='PENDING' WHERE id=?",
            (event_id,),
        )
        return False


def _store() -> FakeStore:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute(
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
    return FakeStore(connection, [])


def _insert(
    store: FakeStore,
    *,
    event_id: str,
    kind: str,
    priority: int,
    status: str = "PENDING",
    lease_until: float | None = None,
) -> None:
    store.connection.execute(
        """
        INSERT INTO events(
            id,kind,payload_json,priority,status,attempts,available_at,created_at,
            lease_owner,lease_until,lease_token,updated_at
        ) VALUES(?,?,?,?,?,0,0,0,NULL,?,NULL,0)
        """,
        (event_id, kind, json.dumps({"task": event_id}), priority, status, lease_until),
    )


def test_claim_filters_kind_even_when_other_event_has_higher_priority() -> None:
    store = _store()
    _insert(store, event_id="other", kind="run.step", priority=100)
    _insert(store, event_id="turn", kind="autonomous.turn", priority=1)

    claim = claim_ready_event_kind(
        store,
        kind="autonomous.turn",
        worker_id="desktop",
        now=10.0,
        lease_seconds=30.0,
    )

    assert claim is not None
    assert claim.event_id == "turn"
    assert claim.kind == "autonomous.turn"
    assert claim.payload == {"task": "turn"}
    other = store.connection.execute(
        "SELECT status FROM events WHERE id='other'"
    ).fetchone()
    assert other["status"] == "PENDING"


def test_claim_reclaims_expired_same_kind_but_not_live_claim() -> None:
    store = _store()
    _insert(
        store,
        event_id="expired",
        kind="autonomous.turn",
        priority=1,
        status="CLAIMED",
        lease_until=9.0,
    )
    _insert(
        store,
        event_id="live",
        kind="autonomous.turn",
        priority=10,
        status="CLAIMED",
        lease_until=20.0,
    )

    claim = claim_ready_event_kind(
        store,
        kind="autonomous.turn",
        worker_id="desktop",
        now=10.0,
        lease_seconds=30.0,
    )

    assert claim is not None
    assert claim.event_id == "expired"
    assert claim.lease_until == 40.0


def test_claim_records_journal_and_returns_none_when_no_ready_match() -> None:
    store = _store()
    _insert(store, event_id="other", kind="run.step", priority=1)

    claim = claim_ready_event_kind(
        store,
        kind="autonomous.turn",
        worker_id="desktop",
        now=10.0,
        lease_seconds=30.0,
    )
    assert claim is None
    assert store.journal == []

    _insert(store, event_id="turn", kind="autonomous.turn", priority=1)
    claim = claim_ready_event_kind(
        store,
        kind="autonomous.turn",
        worker_id="desktop",
        now=10.0,
        lease_seconds=30.0,
    )
    assert claim is not None
    assert store.journal[0][0] == "EVENT_CLAIMED"
    assert store.journal[0][1] == "turn"
