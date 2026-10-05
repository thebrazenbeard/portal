import sqlite3
from pathlib import Path

import pytest

from runner.budgets import BudgetEnvelope
from runner.decompose import admit_child_work
from runner.models import ExactSubject
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore
from runner.recursive_admission import SqliteRecursiveAdmissionStore
from runner.recursive_state import (
    SqliteRecursiveWorkStore,
    _canonical_json,
    _legacy_immutable_digest,
    _work_payload,
)
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def _subject(commit: str) -> ExactSubject:
    return ExactSubject(
        repository="thebrazenbeard/project-runner",
        ref="work/public-safe-portfolio-registry-v2",
        commit=commit,
        path="runner/recursive_state.py",
    )


def _work(
    work_id: str,
    *,
    depth: int,
    parent: str | None,
    commit: str,
    operation: str,
) -> WorkUnit:
    return WorkUnit(
        id=work_id,
        root_frontier_id="frontier-recursive",
        parent_work_id=parent,
        inputs=(_subject(commit),),
        operation=operation,
        required_capabilities=("read", "analyze"),
        collision_keys=("project:project-runner",),
        recursion_depth=depth,
        budget_allocation={
            "children": 2,
            "active": 1,
            "retries": 1,
            "backend_jobs": 1,
        },
        expected_outputs=("verified-work",),
        completion_criteria=("exact-subject-current",),
        status=WorkUnitStatus.PENDING,
        payload={"mode": "recursive-state-test"},
    )


def _budget() -> BudgetEnvelope:
    return BudgetEnvelope(
        lineage_id="lineage-recursive",
        max_depth=3,
        depth=0,
        remaining_children=3,
        remaining_active=3,
        remaining_retries=2,
        remaining_backend_jobs=3,
    )


def _admit(parent: WorkUnit, child: WorkUnit, *, budget: BudgetEnvelope, ancestry=()):
    return admit_child_work(
        parent=parent,
        child=child,
        parent_budget=budget,
        parent_capabilities={"read", "analyze"},
        target_capabilities={"read", "analyze"},
        ancestry_fingerprints=ancestry,
        child_children=2,
        child_active=1,
        child_retries=1,
        child_backend_jobs=1,
    )


def test_recursive_work_survives_restart_with_parent_ancestry_and_scope(tmp_path: Path):
    db = tmp_path / "recursive.db"
    root = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    root_fingerprint = work_unit_fingerprint(root)

    budget_store = SqliteBudgetStore(db)
    assert budget_store.put_initial(_budget()) == 1
    store = SqliteRecursiveWorkStore(db)
    stored_root = store.put_initial(
        work=root,
        lineage_id="lineage-recursive",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={root_fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    child = _work(
        "child",
        depth=1,
        parent=root.id,
        commit="b" * 40,
        operation="REREVIEW",
    )
    admitted = _admit(root, child, budget=_budget())
    child_fingerprint = work_unit_fingerprint(admitted.work)

    admission_store = SqliteRecursiveAdmissionStore(db)
    committed = admission_store.commit_child(
        parent_work_fingerprint=root_fingerprint,
        parent_budget_before=_budget(),
        expected_parent_budget_generation=1,
        target_capabilities={"read", "analyze"},
        admission=admitted,
    )
    assert stored_root.generation == 1
    assert committed.parent_budget_generation == 2
    assert committed.child_budget_generation == 1
    assert committed.child_work_generation == 1
    admission_store.close()
    budget_store.close()
    store.close()

    reopened = SqliteRecursiveWorkStore(db)
    resumed = reopened.get("lineage-recursive", child_fingerprint)
    assert resumed is not None
    assert resumed.work == admitted.work
    assert resumed.parent_fingerprint == root_fingerprint
    assert resumed.ancestry_fingerprints == admitted.ancestry_fingerprints
    assert resumed.budget_scope_id == "work:" + child_fingerprint
    reopened.close()


def test_recursive_work_status_cas_is_scope_local_and_stale_generation_fails(
    tmp_path: Path,
):
    db = tmp_path / "recursive.db"
    root = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(root)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=root,
        lineage_id="lineage-recursive",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    leases = SqliteLeaseStore(db)
    lease = leases.claim(fingerprint, holder="runner", now=0.0, ttl=10.0)
    assert lease is not None
    running = store.compare_and_swap_status(
        lineage_id="lineage-recursive",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=1.0,
    )
    assert running.generation == 2
    assert running.work.status is WorkUnitStatus.RUNNING

    with pytest.raises(ValueError, match="generation"):
        store.compare_and_swap_status(
            lineage_id="lineage-recursive",
            work_fingerprint_value=fingerprint,
            expected_generation=1,
            status=WorkUnitStatus.COMPLETE,
        )
    leases.close()
    store.close()


def test_recursive_work_child_creation_requires_atomic_admission(tmp_path: Path):
    store = SqliteRecursiveWorkStore(tmp_path / "recursive.db")
    parent = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    child = _work(
        "child",
        depth=1,
        parent=parent.id,
        commit="b" * 40,
        operation="REREVIEW",
    )
    admitted = _admit(parent, child, budget=_budget())

    with pytest.raises(ValueError, match="atomic recursive admission"):
        store.put_initial(
            work=admitted.work,
            lineage_id="lineage-recursive",
            budget_scope_id=admitted.child_budget.scope_id,
            parent_fingerprint=work_unit_fingerprint(parent),
            ancestry_fingerprints=admitted.ancestry_fingerprints,
            effective_capabilities={"read", "analyze"},
        )
    store.close()


def test_recursive_work_detects_immutable_state_tampering(tmp_path: Path):
    db = tmp_path / "recursive.db"
    root = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(root)
    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=root,
        lineage_id="lineage-recursive",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    store.connection.execute(
        """
        UPDATE recursive_work_state
        SET ancestry_json = '[]'
        WHERE lineage_id = ? AND work_fingerprint = ?
        """,
        ("lineage-recursive", fingerprint),
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        store.get("lineage-recursive", fingerprint)
    store.close()


def test_post_restart_ancestry_still_rejects_a_to_b_to_a_cycle(tmp_path: Path):
    db = tmp_path / "recursive.db"
    root = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    root_fingerprint = work_unit_fingerprint(root)

    budget_store = SqliteBudgetStore(db)
    budget_store.put_initial(_budget())
    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=root,
        lineage_id="lineage-recursive",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={root_fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    child = _work(
        "child",
        depth=1,
        parent=root.id,
        commit="b" * 40,
        operation="REREVIEW",
    )
    admitted_child = _admit(root, child, budget=_budget())
    child_fingerprint = work_unit_fingerprint(admitted_child.work)
    admission_store = SqliteRecursiveAdmissionStore(db)
    admission_store.commit_child(
        parent_work_fingerprint=root_fingerprint,
        parent_budget_before=_budget(),
        expected_parent_budget_generation=1,
        target_capabilities={"read", "analyze"},
        admission=admitted_child,
    )
    admission_store.close()
    budget_store.close()
    store.close()

    reopened = SqliteRecursiveWorkStore(db)
    resumed_child = reopened.get("lineage-recursive", child_fingerprint)
    assert resumed_child is not None

    root_again = _work(
        "root-renamed",
        depth=2,
        parent=child.id,
        commit="a" * 40,
        operation="INSPECT",
    )
    with pytest.raises(ValueError, match="cycle"):
        admit_child_work(
            parent=resumed_child.work,
            child=root_again,
            parent_budget=admitted_child.child_budget,
            parent_capabilities=set(resumed_child.effective_capabilities),
            target_capabilities={"read", "analyze"},
            ancestry_fingerprints=resumed_child.ancestry_fingerprints,
            child_children=0,
            child_active=0,
            child_retries=0,
            child_backend_jobs=0,
        )
    reopened.close()


def test_same_semantic_work_isolated_between_lineages(tmp_path: Path):
    db = tmp_path / "recursive.db"
    work = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)
    store = SqliteRecursiveWorkStore(db)

    for lineage_id in ("lineage-a", "lineage-b"):
        stored = store.put_initial(
            work=work,
            lineage_id=lineage_id,
            budget_scope_id="root",
            parent_fingerprint=None,
            ancestry_fingerprints={fingerprint},
            effective_capabilities={"read", "analyze"},
        )
        assert stored.lineage_id == lineage_id

    with pytest.raises(ValueError, match="already exists"):
        store.put_initial(
            work=work,
            lineage_id="lineage-a",
            budget_scope_id="root",
            parent_fingerprint=None,
            ancestry_fingerprints={fingerprint},
            effective_capabilities={"read", "analyze"},
        )
    store.close()


def test_terminal_recursive_work_cannot_reset_or_transition(tmp_path: Path):
    store = SqliteRecursiveWorkStore(tmp_path / "recursive.db")
    work = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)
    store.put_initial(
        work=work,
        lineage_id="lineage-terminal",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(tmp_path / "recursive.db")
    lease = leases.claim(fingerprint, holder="terminal-test", now=0.0, ttl=10.0)
    assert lease is not None
    running = store.compare_and_swap_status(
        lineage_id="lineage-terminal",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=0.25,
    )
    assert running.generation == 2
    verifying = store.compare_and_swap_status(
        lineage_id="lineage-terminal",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=lease,
        now=0.5,
    )
    assert verifying.generation == 3
    complete = store.finalize_terminal_status(
        lineage_id="lineage-terminal",
        work_fingerprint_value=fingerprint,
        expected_generation=3,
        status=WorkUnitStatus.COMPLETE,
        lease=lease,
        now=1.0,
    )
    assert complete.generation == 4

    with pytest.raises(ValueError, match="terminal"):
        store.compare_and_swap_status(
            lineage_id="lineage-terminal",
            work_fingerprint_value=fingerprint,
            expected_generation=4,
            status=WorkUnitStatus.PENDING,
        )
    with pytest.raises(ValueError, match="terminal"):
        store.compare_and_swap_status(
            lineage_id="lineage-terminal",
            work_fingerprint_value=fingerprint,
            expected_generation=4,
            status=WorkUnitStatus.SUPERSEDED,
        )
    leases.close()
    store.close()


def test_nonpending_recursive_work_cannot_reset_to_pending(tmp_path: Path):
    store = SqliteRecursiveWorkStore(tmp_path / "recursive.db")
    work = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)
    store.put_initial(
        work=work,
        lineage_id="lineage-running",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(tmp_path / "recursive.db")
    lease = leases.claim(fingerprint, holder="running-test", now=0.0, ttl=10.0)
    assert lease is not None
    running = store.compare_and_swap_status(
        lineage_id="lineage-running",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=1.0,
    )
    assert running.generation == 2

    with pytest.raises(ValueError, match="RUNNING -> PENDING"):
        store.compare_and_swap_status(
            lineage_id="lineage-running",
            work_fingerprint_value=fingerprint,
            expected_generation=2,
            status=WorkUnitStatus.PENDING,
        )
    leases.close()
    store.close()


def test_same_status_update_is_idempotent_without_generation_churn(tmp_path: Path):
    store = SqliteRecursiveWorkStore(tmp_path / "recursive.db")
    work = _work(
        "root",
        depth=0,
        parent=None,
        commit="a" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)
    stored = store.put_initial(
        work=work,
        lineage_id="lineage-idempotent",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    same = store.compare_and_swap_status(
        lineage_id="lineage-idempotent",
        work_fingerprint_value=fingerprint,
        expected_generation=stored.generation,
        status=WorkUnitStatus.PENDING,
    )
    assert same.generation == stored.generation == 1
    assert same.work.status is WorkUnitStatus.PENDING
    store.close()


def _write_legacy_recursive_root(db: Path, *, tamper: bool = False):
    work = _work(
        "legacy-root",
        depth=0,
        parent=None,
        commit="9" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)
    work_json = _canonical_json(_work_payload(work))
    ancestry_json = _canonical_json([fingerprint])
    digest = _legacy_immutable_digest(
        work_json=work_json,
        lineage_id="legacy-lineage",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_json=ancestry_json,
    )

    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE recursive_work_state (
            lineage_id TEXT NOT NULL,
            work_fingerprint TEXT NOT NULL,
            work_json TEXT NOT NULL,
            budget_scope_id TEXT NOT NULL,
            parent_fingerprint TEXT,
            ancestry_json TEXT NOT NULL,
            immutable_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            generation INTEGER NOT NULL,
            PRIMARY KEY (lineage_id, work_fingerprint)
        );
        """
    )
    connection.execute(
        """
        INSERT INTO recursive_work_state (
            lineage_id, work_fingerprint, work_json, budget_scope_id,
            parent_fingerprint, ancestry_json, immutable_sha256,
            status, generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-lineage",
            fingerprint,
            work_json,
            "root",
            None,
            ancestry_json,
            digest,
            WorkUnitStatus.PENDING.value,
            7,
        ),
    )
    if tamper:
        connection.execute(
            """
            UPDATE recursive_work_state
            SET work_json = replace(work_json, '"INSPECT"', '"TAMPERED"')
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            ("legacy-lineage", fingerprint),
        )
    connection.commit()
    connection.close()
    return work, fingerprint


def test_legacy_recursive_state_migrates_to_conservative_capability_ceiling(
    tmp_path: Path,
):
    db = tmp_path / "legacy-recursive.db"
    work, fingerprint = _write_legacy_recursive_root(db)

    store = SqliteRecursiveWorkStore(db)
    migrated = store.get("legacy-lineage", fingerprint)
    assert migrated is not None
    assert migrated.work == work
    assert migrated.generation == 7
    assert migrated.effective_capabilities == tuple(
        sorted(set(work.required_capabilities))
    )

    columns = {
        row[1]
        for row in store.connection.execute(
            "PRAGMA table_info(recursive_work_state)"
        )
    }
    assert "effective_capabilities_json" in columns
    store.close()


def test_legacy_recursive_state_tamper_is_rejected_before_capability_migration(
    tmp_path: Path,
):
    db = tmp_path / "tampered-legacy-recursive.db"
    _write_legacy_recursive_root(db, tamper=True)

    with pytest.raises(ValueError, match="legacy recursive work state digest mismatch"):
        SqliteRecursiveWorkStore(db)

    # Failure must not leave the database locked.
    connection = sqlite3.connect(db)
    connection.execute("SELECT 1").fetchone()
    connection.close()


def test_terminal_completion_rejects_stale_reclaimed_fence(tmp_path: Path):
    db = tmp_path / "recursive.db"
    work = _work(
        "root-fence",
        depth=0,
        parent=None,
        commit="7" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-fence",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(db)
    stale = leases.claim(fingerprint, holder="A", now=0.0, ttl=10.0)
    assert stale is not None and stale.fencing_token == 1
    running = store.compare_and_swap_status(
        lineage_id="lineage-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=stale,
        now=1.0,
    )
    assert running.generation == 2
    verifying = store.compare_and_swap_status(
        lineage_id="lineage-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=stale,
        now=2.0,
    )
    assert verifying.generation == 3

    current = leases.claim(fingerprint, holder="B", now=11.0, ttl=10.0)
    assert current is not None and current.fencing_token == 2

    with pytest.raises(ValueError, match="fencing token is stale"):
        store.finalize_terminal_status(
            lineage_id="lineage-fence",
            work_fingerprint_value=fingerprint,
            expected_generation=3,
            status=WorkUnitStatus.COMPLETE,
            lease=stale,
            now=12.0,
        )

    unchanged = store.get("lineage-fence", fingerprint)
    assert unchanged is not None
    assert unchanged.work.status is WorkUnitStatus.VERIFYING
    assert unchanged.generation == 3

    completed = store.finalize_terminal_status(
        lineage_id="lineage-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=3,
        status=WorkUnitStatus.COMPLETE,
        lease=current,
        now=12.0,
    )
    assert completed.work.status is WorkUnitStatus.COMPLETE
    assert completed.generation == 4
    leases.close()
    store.close()


def test_terminal_failure_requires_released_exact_fence(tmp_path: Path):
    db = tmp_path / "recursive.db"
    work = _work(
        "root-failure-fence",
        depth=0,
        parent=None,
        commit="8" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-failure-fence",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    leases = SqliteLeaseStore(db)
    lease = leases.claim(fingerprint, holder="worker", now=0.0, ttl=10.0)
    assert lease is not None
    running = store.compare_and_swap_status(
        lineage_id="lineage-failure-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=0.5,
    )
    assert running.generation == 2
    failed = store.finalize_terminal_status(
        lineage_id="lineage-failure-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.FAILED_DETERMINISTIC,
        lease=lease,
        now=1.0,
    )
    assert failed.work.status is WorkUnitStatus.FAILED_DETERMINISTIC
    assert failed.generation == 3
    leases.close()
    store.close()


def test_unfenced_terminal_status_is_rejected(tmp_path: Path):
    db = tmp_path / "recursive.db"
    work = _work(
        "root-unfenced",
        depth=0,
        parent=None,
        commit="6" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-unfenced",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(db)
    lease = leases.claim(fingerprint, holder="unfenced-test", now=0.0, ttl=10.0)
    assert lease is not None
    store.compare_and_swap_status(
        lineage_id="lineage-unfenced",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=0.25,
    )
    store.compare_and_swap_status(
        lineage_id="lineage-unfenced",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=lease,
        now=0.5,
    )
    with pytest.raises(ValueError, match="requires atomic finalization"):
        store.compare_and_swap_status(
            lineage_id="lineage-unfenced",
            work_fingerprint_value=fingerprint,
            expected_generation=3,
            status=WorkUnitStatus.COMPLETE,
        )
    leases.close()
    store.close()


def test_active_status_requires_current_unexpired_fence(tmp_path: Path):
    db = tmp_path / "active-fence.db"
    work = _work(
        "root-active-fence",
        depth=0,
        parent=None,
        commit="5" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-active-fence",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    with pytest.raises(ValueError, match="requires a lease fence"):
        store.compare_and_swap_status(
            lineage_id="lineage-active-fence",
            work_fingerprint_value=fingerprint,
            expected_generation=1,
            status=WorkUnitStatus.RUNNING,
            now=1.0,
        )

    leases = SqliteLeaseStore(db)
    first = leases.claim(fingerprint, holder="A", now=0.0, ttl=10.0)
    assert first is not None

    with pytest.raises(ValueError, match="expired"):
        store.compare_and_swap_status(
            lineage_id="lineage-active-fence",
            work_fingerprint_value=fingerprint,
            expected_generation=1,
            status=WorkUnitStatus.RUNNING,
            lease=first,
            now=10.0,
        )

    second = leases.claim(fingerprint, holder="B", now=11.0, ttl=10.0)
    assert second is not None and second.fencing_token == first.fencing_token + 1

    with pytest.raises(ValueError, match="fencing token is stale"):
        store.compare_and_swap_status(
            lineage_id="lineage-active-fence",
            work_fingerprint_value=fingerprint,
            expected_generation=1,
            status=WorkUnitStatus.RUNNING,
            lease=first,
            now=12.0,
        )

    running = store.compare_and_swap_status(
        lineage_id="lineage-active-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=second,
        now=12.0,
    )
    assert running.work.status is WorkUnitStatus.RUNNING
    assert running.generation == 2

    verifying = store.compare_and_swap_status(
        lineage_id="lineage-active-fence",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=second,
        now=13.0,
    )
    assert verifying.work.status is WorkUnitStatus.VERIFYING
    assert verifying.generation == 3

    leases.close()
    store.close()


def test_active_lifecycle_rejects_forward_skip_and_backward_transition(tmp_path: Path):
    db = tmp_path / "ordered-lifecycle.db"
    work = _work(
        "root-ordered",
        depth=0,
        parent=None,
        commit="4" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-ordered",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(db)
    lease = leases.claim(fingerprint, holder="ordered", now=0.0, ttl=10.0)
    assert lease is not None

    with pytest.raises(ValueError, match="PENDING -> VERIFYING"):
        store.compare_and_swap_status(
            lineage_id="lineage-ordered",
            work_fingerprint_value=fingerprint,
            expected_generation=1,
            status=WorkUnitStatus.VERIFYING,
            lease=lease,
            now=1.0,
        )

    running = store.compare_and_swap_status(
        lineage_id="lineage-ordered",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=1.0,
    )
    assert running.generation == 2
    verifying = store.compare_and_swap_status(
        lineage_id="lineage-ordered",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=lease,
        now=2.0,
    )
    assert verifying.generation == 3

    with pytest.raises(ValueError, match="VERIFYING -> CLAIMED"):
        store.compare_and_swap_status(
            lineage_id="lineage-ordered",
            work_fingerprint_value=fingerprint,
            expected_generation=3,
            status=WorkUnitStatus.CLAIMED,
            lease=lease,
            now=3.0,
        )

    leases.close()
    store.close()


def test_atomic_terminal_finalization_commits_lease_and_work_together(tmp_path: Path):
    db = tmp_path / "atomic-finalize.db"
    work = _work(
        "root-atomic-finalize",
        depth=0,
        parent=None,
        commit="3" * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id="lineage-atomic-finalize",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(db)
    lease = leases.claim(fingerprint, holder="atomic-finalizer", now=0.0, ttl=10.0)
    assert lease is not None

    store.compare_and_swap_status(
        lineage_id="lineage-atomic-finalize",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=lease,
        now=1.0,
    )
    store.compare_and_swap_status(
        lineage_id="lineage-atomic-finalize",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=WorkUnitStatus.VERIFYING,
        lease=lease,
        now=2.0,
    )

    with pytest.raises(ValueError, match="requires atomic finalization"):
        store.compare_and_swap_status(
            lineage_id="lineage-atomic-finalize",
            work_fingerprint_value=fingerprint,
            expected_generation=3,
            status=WorkUnitStatus.COMPLETE,
            lease=lease,
        )

    before = store.get("lineage-atomic-finalize", fingerprint)
    assert before is not None
    assert before.work.status is WorkUnitStatus.VERIFYING
    assert before.generation == 3

    finalized = store.finalize_terminal_status(
        lineage_id="lineage-atomic-finalize",
        work_fingerprint_value=fingerprint,
        expected_generation=3,
        status=WorkUnitStatus.COMPLETE,
        lease=lease,
        now=3.0,
    )
    assert finalized.work.status is WorkUnitStatus.COMPLETE
    assert finalized.generation == 4

    lease_row = leases.connection.execute(
        """
        SELECT holder, fencing_token, completed
        FROM leases
        WHERE work_fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()
    assert lease_row == ("atomic-finalizer", lease.fencing_token, 1)

    leases.close()
    store.close()


@pytest.mark.parametrize(
    "outcome_status",
    (
        WorkUnitStatus.FAILED_RETRYABLE,
        WorkUnitStatus.OUTCOME_UNKNOWN,
    ),
)
def test_retryable_and_unknown_outcomes_require_current_fence(
    tmp_path: Path,
    outcome_status: WorkUnitStatus,
):
    db = tmp_path / f"{outcome_status.value.lower()}.db"
    work = _work(
        f"root-{outcome_status.value.lower()}",
        depth=0,
        parent=None,
        commit=("2" if outcome_status is WorkUnitStatus.FAILED_RETRYABLE else "1") * 40,
        operation="INSPECT",
    )
    fingerprint = work_unit_fingerprint(work)

    store = SqliteRecursiveWorkStore(db)
    store.put_initial(
        work=work,
        lineage_id=f"lineage-{outcome_status.value.lower()}",
        budget_scope_id="root",
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )
    leases = SqliteLeaseStore(db)
    first = leases.claim(fingerprint, holder="A", now=0.0, ttl=10.0)
    assert first is not None

    running = store.compare_and_swap_status(
        lineage_id=f"lineage-{outcome_status.value.lower()}",
        work_fingerprint_value=fingerprint,
        expected_generation=1,
        status=WorkUnitStatus.RUNNING,
        lease=first,
        now=1.0,
    )
    assert running.generation == 2

    with pytest.raises(ValueError, match="requires a lease fence"):
        store.compare_and_swap_status(
            lineage_id=f"lineage-{outcome_status.value.lower()}",
            work_fingerprint_value=fingerprint,
            expected_generation=2,
            status=outcome_status,
            now=2.0,
        )

    owned = store.compare_and_swap_status(
        lineage_id=f"lineage-{outcome_status.value.lower()}",
        work_fingerprint_value=fingerprint,
        expected_generation=2,
        status=outcome_status,
        lease=first,
        now=2.0,
    )
    assert owned.work.status is outcome_status
    assert owned.generation == 3

    second = leases.claim(fingerprint, holder="B", now=11.0, ttl=10.0)
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1

    with pytest.raises(ValueError, match="fencing token is stale"):
        store.compare_and_swap_status(
            lineage_id=f"lineage-{outcome_status.value.lower()}",
            work_fingerprint_value=fingerprint,
            expected_generation=3,
            status=WorkUnitStatus.RUNNING,
            lease=first,
            now=12.0,
        )

    resumed = store.compare_and_swap_status(
        lineage_id=f"lineage-{outcome_status.value.lower()}",
        work_fingerprint_value=fingerprint,
        expected_generation=3,
        status=WorkUnitStatus.RUNNING,
        lease=second,
        now=12.0,
    )
    assert resumed.work.status is WorkUnitStatus.RUNNING
    assert resumed.generation == 4

    leases.close()
    store.close()
