from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3

import pytest

from portal.desktop_pre_active import process_one_volition_signal


class Store:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
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
        self.journal: list[dict[str, object]] = []
        self.acked: list[str] = []
        self.failed: list[dict[str, object]] = []

    def add_signal(self, payload: dict[str, object]) -> None:
        self.connection.execute(
            """
            INSERT INTO events(
                id,kind,payload_json,priority,status,attempts,
                available_at,created_at,updated_at
            ) VALUES('signal-1','volition.signal',?,0,'PENDING',0,0,0,0)
            """,
            (json.dumps(payload),),
        )

    def append_journal(self, *, event_type, subject_id, payload, now) -> None:
        self.journal.append(
            {
                "event_type": event_type,
                "subject_id": subject_id,
                "payload": payload,
                "now": now,
            }
        )

    @contextmanager
    def active_claim_transaction(
        self,
        event_id,
        *,
        worker_id,
        lease_token,
        now,
        validate=None,
    ):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT 1 FROM events
                WHERE id=? AND status='CLAIMED'
                  AND lease_owner=? AND lease_token=?
                """,
                (event_id, worker_id, lease_token),
            ).fetchone()
            if row is None:
                raise RuntimeError("claim missing")
            yield float(now() if callable(now) else now)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def ack_event(self, event_id, *, worker_id, lease_token, now) -> None:
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


class Bridge:
    def __init__(self, store: Store) -> None:
        self.store = store

    def process_signal_event(self, *, source_event_id, payload, now):
        return {
            "source_event_id": source_event_id,
            "cognition_event_id": "turn-1",
            "effect_authority": False,
        }


class InvalidBridge:
    def __init__(self, store: Store) -> None:
        self.store = store

    def process_signal_event(self, *, source_event_id, payload, now):
        raise ValueError("bad signal")


class FailingBridge:
    def __init__(self, store: Store) -> None:
        self.store = store

    def process_signal_event(self, *, source_event_id, payload, now):
        raise RuntimeError("bridge failed")


def test_valid_volition_signal_is_processed_and_acked() -> None:
    store = Store()
    store.add_signal({"target": "x"})

    result = process_one_volition_signal(
        store,
        now=10.0,
        bridge_factory=Bridge,
        invalid_signal_types=(ValueError,),
    )

    assert result is not None
    assert result["cognition_event_id"] == "turn-1"
    assert store.acked == ["signal-1"]
    assert store.failed == []


def test_invalid_volition_signal_is_rejected_and_acked_not_retried() -> None:
    store = Store()
    store.add_signal({"target": "x"})

    result = process_one_volition_signal(
        store,
        now=10.0,
        bridge_factory=InvalidBridge,
        invalid_signal_types=(ValueError,),
    )

    assert result is not None
    assert result["status"] == "REJECTED"
    assert store.acked == ["signal-1"]
    assert store.failed == []
    assert any(x["event_type"] == "EVENT_REJECTED" for x in store.journal)


def test_unexpected_volition_failure_is_retry_scheduled() -> None:
    store = Store()
    store.add_signal({"target": "x"})

    with pytest.raises(RuntimeError, match="bridge failed"):
        process_one_volition_signal(
            store,
            now=10.0,
            retry_delay_seconds=30.0,
            max_attempts=4,
            bridge_factory=FailingBridge,
            invalid_signal_types=(ValueError,),
        )

    assert store.acked == []
    assert store.failed[0]["retry_at"] == 40.0
    assert store.failed[0]["max_attempts"] == 4
