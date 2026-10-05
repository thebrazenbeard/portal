import sqlite3

import pytest

from runner.budgets import BudgetEnvelope
from runner import sqlite_state


def _budget():
    return BudgetEnvelope(
        lineage_id="lineage-close",
        max_depth=2,
        depth=0,
        remaining_children=1,
        remaining_active=1,
        remaining_retries=1,
        remaining_backend_jobs=1,
    )


def _assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_context_managed_budget_connections_are_actually_closed(
    monkeypatch,
    tmp_path,
):
    opened = []
    real_connect = sqlite_state._connect

    def tracked_connect(path):
        connection = real_connect(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite_state, "_connect", tracked_connect)

    store = sqlite_state.SQLiteLineageBudgetStore(tmp_path / "state.sqlite3")
    store.create(_budget())
    assert store.get("lineage-close") is not None

    assert opened
    for connection in opened:
        _assert_closed(connection)


def test_context_managed_lease_connections_are_actually_closed(
    monkeypatch,
    tmp_path,
):
    opened = []
    real_connect = sqlite_state._connect

    def tracked_connect(path):
        connection = real_connect(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite_state, "_connect", tracked_connect)

    store = sqlite_state.SQLiteLeaseStore(tmp_path / "state.sqlite3")
    lease = store.claim("fp-close", holder="worker", now=10.0, ttl=10.0)
    assert lease is not None
    refreshed = store.heartbeat(lease, now=11.0, ttl=10.0)
    assert refreshed is not None
    assert store.release(refreshed, now=12.0) is True

    assert opened
    for connection in opened:
        _assert_closed(connection)
