import tempfile
from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.leases import Lease
from runner.sqlite_state import SQLiteLeaseStore, SQLiteLineageBudgetStore


def _budget():
    return BudgetEnvelope(
        lineage_id="lineage-1",
        max_depth=4,
        depth=0,
        remaining_children=5,
        remaining_active=3,
        remaining_retries=2,
        remaining_backend_jobs=3,
    )


def test_budget_state_survives_reopen_and_cas_prevents_stale_reset():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "state.sqlite3"
        store = SQLiteLineageBudgetStore(db)
        created = store.create(_budget())
        assert created.generation == 0

        reserved = store.reserve(
            "lineage-1",
            expected_generation=0,
            depth=0,
            children=1,
            active=1,
            retries=0,
            backend_jobs=1,
        )
        assert reserved.generation == 1
        assert reserved.envelope.remaining_children == 4
        assert reserved.envelope.remaining_active == 2
        assert reserved.envelope.remaining_backend_jobs == 2

        reopened = SQLiteLineageBudgetStore(db)
        observed = reopened.get("lineage-1")
        assert observed is not None
        assert observed.generation == 1
        assert observed.envelope.remaining_children == 4

        with pytest.raises(ValueError, match="generation"):
            reopened.reserve(
                "lineage-1",
                expected_generation=0,
                depth=0,
                children=0,
                active=1,
                retries=0,
                backend_jobs=1,
            )


def test_budget_depth_and_overallocation_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        store = SQLiteLineageBudgetStore(Path(td) / "state.sqlite3")
        store.create(_budget())
        with pytest.raises(ValueError, match="depth"):
            store.reserve(
                "lineage-1",
                expected_generation=0,
                depth=5,
                children=0,
                active=0,
                retries=0,
                backend_jobs=0,
            )
        with pytest.raises(ValueError, match="active"):
            store.reserve(
                "lineage-1",
                expected_generation=0,
                depth=0,
                children=0,
                active=4,
                retries=0,
                backend_jobs=0,
            )


def test_sqlite_lease_reclaim_increments_fence_across_restart():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "state.sqlite3"
        first_store = SQLiteLeaseStore(db)
        first = first_store.claim("fp", holder="a", now=100.0, ttl=10.0)
        assert first is not None
        assert first.fencing_token == 1

        second_store = SQLiteLeaseStore(db)
        assert second_store.claim("fp", holder="b", now=105.0, ttl=10.0) is None
        reclaimed = second_store.claim("fp", holder="b", now=111.0, ttl=10.0)
        assert reclaimed is not None
        assert reclaimed.fencing_token == 2

        assert second_store.complete(first, now=112.0) is False
        assert second_store.complete(reclaimed, now=112.0) is True


def test_sqlite_completed_work_stays_unclaimable_after_restart():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "state.sqlite3"
        store = SQLiteLeaseStore(db)
        lease = store.claim("fp", holder="a", now=10.0, ttl=30.0)
        assert lease is not None
        assert store.complete(lease, now=11.0) is True

        reopened = SQLiteLeaseStore(db)
        assert reopened.claim("fp", holder="b", now=100.0, ttl=10.0) is None


def test_lineage_wide_store_rejects_child_budget_scope():
    with tempfile.TemporaryDirectory() as td:
        store = SQLiteLineageBudgetStore(Path(td) / "state.sqlite3")
        child = BudgetEnvelope(
            lineage_id="lineage-1",
            max_depth=4,
            depth=1,
            remaining_children=1,
            remaining_active=1,
            remaining_retries=0,
            remaining_backend_jobs=1,
            scope_id="work:" + ("a" * 64),
        )
        with pytest.raises(ValueError, match="root budget scope"):
            store.create(child)
