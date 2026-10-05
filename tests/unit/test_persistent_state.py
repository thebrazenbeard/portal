import sqlite3
from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore


def _budget():
    return BudgetEnvelope(
        lineage_id="lineage-1",
        max_depth=4,
        depth=1,
        remaining_children=5,
        remaining_active=3,
        remaining_retries=2,
        remaining_backend_jobs=4,
    )


def test_budget_store_survives_reopen_and_uses_generation_cas(tmp_path: Path):
    db = tmp_path / "state.db"
    store = SqliteBudgetStore(db)
    generation = store.put_initial(_budget())
    assert generation == 1
    store.close()

    reopened = SqliteBudgetStore(db)
    loaded, loaded_generation = reopened.get("lineage-1")
    assert loaded == _budget()
    assert loaded_generation == 1

    updated = BudgetEnvelope(
        lineage_id="lineage-1",
        max_depth=4,
        depth=1,
        remaining_children=4,
        remaining_active=2,
        remaining_retries=2,
        remaining_backend_jobs=3,
    )
    assert reopened.compare_and_swap(updated, expected_generation=1) == 2
    with pytest.raises(ValueError, match="generation"):
        reopened.compare_and_swap(_budget(), expected_generation=1)
    reopened.close()


def test_sqlite_lease_reclaim_increments_fence_after_restart(tmp_path: Path):
    db = tmp_path / "state.db"
    store = SqliteLeaseStore(db)
    first = store.claim("fp-1", holder="a", now=100.0, ttl=10.0)
    assert first is not None
    assert first.fencing_token == 1
    store.close()

    reopened = SqliteLeaseStore(db)
    second = reopened.claim("fp-1", holder="b", now=111.0, ttl=10.0)
    assert second is not None
    assert second.fencing_token == 2
    assert reopened.complete(first, now=112.0) is False
    assert reopened.complete(second, now=112.0) is True
    reopened.close()


def test_sqlite_lease_nonexpired_claim_is_atomic(tmp_path: Path):
    db = tmp_path / "state.db"
    a = SqliteLeaseStore(db)
    b = SqliteLeaseStore(db)

    first = a.claim("fp-1", holder="a", now=100.0, ttl=30.0)
    second = b.claim("fp-1", holder="b", now=101.0, ttl=30.0)

    assert first is not None
    assert second is None

    a.close()
    b.close()


def test_child_budget_scope_requires_atomic_recursive_admission(tmp_path: Path):
    store = SqliteBudgetStore(tmp_path / "state.db")
    child = BudgetEnvelope(
        lineage_id="recursive-lineage",
        max_depth=3,
        depth=1,
        remaining_children=1,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
        scope_id="work:" + ("a" * 64),
    )

    with pytest.raises(ValueError, match="atomic recursive admission"):
        store.put_initial(child)
    store.close()

def test_budget_store_migrates_legacy_lineage_row_to_root_scope(tmp_path: Path):
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE lineage_budgets (
            lineage_id TEXT PRIMARY KEY,
            max_depth INTEGER NOT NULL,
            depth INTEGER NOT NULL,
            remaining_children INTEGER NOT NULL,
            remaining_active INTEGER NOT NULL,
            remaining_retries INTEGER NOT NULL,
            remaining_backend_jobs INTEGER NOT NULL,
            generation INTEGER NOT NULL
        );
        INSERT INTO lineage_budgets VALUES (
            'legacy-lineage', 4, 0, 5, 3, 2, 4, 7
        );
        """
    )
    connection.commit()
    connection.close()

    store = SqliteBudgetStore(db)
    observed, generation = store.get("legacy-lineage")
    assert observed.scope_id == "root"
    assert observed.lineage_id == "legacy-lineage"
    assert observed.remaining_backend_jobs == 4
    assert generation == 7

    columns = {
        row[1]
        for row in store.connection.execute("PRAGMA table_info(lineage_budgets)")
    }
    assert "scope_id" in columns
    store.close()
