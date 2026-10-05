from dataclasses import replace
from pathlib import Path

import pytest

from runner.backends import MockBackend
from runner.budgets import BudgetEnvelope
from runner.collisions import partition_collision_groups
from runner.dedup import deduplicate_frontiers
from runner.dispatch import dispatch_ready, frontier_to_work_unit
from runner.durable_dispatch import (
    SqliteDispatchAdmissionStore,
    execute_admitted,
    verify_observed_attempt,
)
from runner.frontier import derive_frontiers
from runner.leases import InMemoryLeaseStore
from runner.models import ExactSubject, FrontierStatus
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.propagate import derive_invalidations
from runner.registry import load_dependencies, load_observations
from runner.verify import verify_attempt
from runner.work_units import WorkUnitStatus, work_unit_fingerprint


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BEFORE = FIXTURES / "m6-hc-before.yaml"
SUBSTANTIVE = FIXTURES / "m6-hc-substantive.yaml"
CURRENT = FIXTURES / "m6-hc-current.yaml"
DEPENDENCIES = FIXTURES / "m6-hc-transcendence-dependencies.yaml"


def _frontiers(before: Path, after: Path):
    previous = load_observations(before)
    current = load_observations(after)
    edges = load_dependencies(DEPENDENCIES)
    invalidations = derive_invalidations(previous, current, edges)
    frontiers = derive_frontiers(
        invalidations,
        capability_lookup={"transcendence": {"analyze"}},
    )
    return invalidations, frontiers


def _budget() -> BudgetEnvelope:
    return BudgetEnvelope(
        lineage_id="m6-hc-transcendence-proof",
        max_depth=1,
        depth=0,
        remaining_children=0,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
    )


def test_substantive_hc_change_derives_one_bounded_transcendence_inspection():
    invalidations, frontiers = _frontiers(BEFORE, SUBSTANTIVE)

    assert len(invalidations) == 1
    invalidation = invalidations[0]
    assert invalidation.provider == "hc-brain"
    assert invalidation.consumer == "transcendence"
    assert invalidation.reaction.value == "INSPECT"
    assert invalidation.changed_subject.commit == (
        "fdff1094c4f6388c6ef2c94c193ec887b141d7fb"
    )

    deduped = deduplicate_frontiers(frontiers + frontiers)
    assert len(deduped) == 1
    frontier = deduped[0]
    assert frontier.project == "transcendence"
    assert frontier.status is FrontierStatus.READY
    assert frontier.required_capabilities == ("analyze",)
    assert frontier.collision_keys == ("project:transcendence",)

    groups = partition_collision_groups(deduped)
    assert len(groups) == 1
    assert tuple(item.id for item in groups[0]) == (frontier.id,)


def test_substantive_frontier_is_superseded_when_hc_main_moves_again():
    _, frontiers = _frontiers(BEFORE, SUBSTANTIVE)
    store = InMemoryLeaseStore()
    backend = MockBackend()
    batch = dispatch_ready(
        frontiers,
        lease_store=store,
        backend=backend,
        budget=_budget(),
        holder="m6-proof-stale",
        now=0.0,
        lease_ttl=60.0,
    )

    assert len(batch.attempts) == 1
    assert batch.remaining_budget.remaining_active == 0
    assert batch.remaining_budget.remaining_backend_jobs == 0
    attempt = batch.attempts[0]
    assert attempt.lease.fencing_token == 1
    assert attempt.result.succeeded

    current = ExactSubject(
        repository="thebrazenbeard/hc-brain",
        ref="main",
        commit="618245b54fb923c7a204892c6953ab6d1c5dac57",
    )
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=1.0,
        current_subject_reader=lambda subject: current,
        evidence_verifier=lambda work, result: (
            result.succeeded and "mock-backend" in result.evidence
        ),
    )

    assert outcome.status is WorkUnitStatus.SUPERSEDED
    assert outcome.reason == "exact subject moved after execution"


def test_refreshed_current_hc_frontier_completes_with_restart_safe_state(tmp_path):
    _, frontiers = _frontiers(SUBSTANTIVE, CURRENT)
    db = tmp_path / "m6-proof.sqlite3"
    budget_store = SqliteBudgetStore(db)
    generation = budget_store.put_initial(_budget())
    persisted_budget, observed_generation = budget_store.get(
        "m6-hc-transcendence-proof"
    )
    assert observed_generation == generation == 1

    lease_store = SqliteLeaseStore(db)
    recursive_store = SqliteRecursiveWorkStore(db)
    backend = MockBackend()

    work = frontier_to_work_unit(frontiers[0], depth=persisted_budget.depth)
    fingerprint = work_unit_fingerprint(work)
    recursive_store.put_initial(
        work=work,
        lineage_id=persisted_budget.lineage_id,
        budget_scope_id=persisted_budget.scope_id,
        parent_fingerprint=None,
        ancestry_fingerprints={fingerprint},
        effective_capabilities={"read", "analyze"},
    )

    dispatch_store = SqliteDispatchAdmissionStore(db)
    admitted = dispatch_store.admit(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=fingerprint,
        budget_scope_id=persisted_budget.scope_id,
        expected_budget_generation=generation,
        expected_work_generation=1,
        holder="m6-proof-current",
        now=0.0,
        ttl=60.0,
    )
    new_generation = admitted.budget_generation
    assert new_generation == 2
    assert admitted.budget_after.remaining_active == 0
    assert admitted.budget_after.remaining_backend_jobs == 0
    assert dispatch_store.unresolved_attempts()[0].phase == "ADMITTED"

    attempt = execute_admitted(
        frontier=frontiers[0],
        admission=admitted,
        backend=backend,
        journal=dispatch_store,
        recorded_at=0.1,
    )
    unresolved_after_result = dispatch_store.unresolved_attempts()
    assert len(unresolved_after_result) == 1
    assert unresolved_after_result[0].phase == "RESULT_RECORDED"
    assert attempt.frontier.subject.commit == (
        "618245b54fb923c7a204892c6953ab6d1c5dac57"
    )
    assert attempt.lease.fencing_token == 1

    running_work = recursive_store.compare_and_swap_status(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=work_unit_fingerprint(attempt.work),
        expected_generation=admitted.work_generation,
        status=WorkUnitStatus.RUNNING,
        lease=attempt.lease,
        now=0.25,
    )
    assert running_work.generation == 3
    verifying_work = recursive_store.compare_and_swap_status(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=work_unit_fingerprint(attempt.work),
        expected_generation=3,
        status=WorkUnitStatus.VERIFYING,
        lease=attempt.lease,
        now=0.5,
    )
    assert verifying_work.generation == 4

    still_verifying = verify_observed_attempt(
        attempt,
        lease_store=lease_store,
        now=1.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: False,
        manage_lease=False,
    )
    assert still_verifying.status is WorkUnitStatus.VERIFYING
    assert dispatch_store.unresolved_attempts()[0].phase == "RESULT_RECORDED"

    completed = verify_observed_attempt(
        attempt,
        lease_store=lease_store,
        now=2.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: (
            result.succeeded and "mock-backend" in result.evidence
        ),
        manage_lease=False,
    )
    assert completed.status is WorkUnitStatus.COMPLETE
    assert completed.reason == (
        "exact subject current and completion evidence verified"
    )
    assert dispatch_store.unresolved_attempts()[0].phase == "RESULT_RECORDED"

    # A failed terminal CAS must roll back both work/lease mutation and journal PASS.
    with pytest.raises(ValueError, match="generation mismatch"):
        dispatch_store.finalize_terminal_verification(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=attempt.lease.fencing_token,
            expected_work_generation=999,
            lease=attempt.lease,
            status=completed.status,
            reason=completed.reason,
            verified_at=2.0,
        )
    unchanged = recursive_store.get(
        persisted_budget.lineage_id,
        fingerprint,
    )
    assert unchanged is not None
    assert unchanged.generation == 4
    assert unchanged.work.status is WorkUnitStatus.VERIFYING
    assert dispatch_store.unresolved_attempts()[0].phase == "RESULT_RECORDED"

    terminal_generation = dispatch_store.finalize_terminal_verification(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=attempt.lease.fencing_token,
        expected_work_generation=4,
        lease=attempt.lease,
        status=completed.status,
        reason=completed.reason,
        verified_at=2.0,
    )
    assert terminal_generation == 5
    durable_complete = recursive_store.get(
        persisted_budget.lineage_id,
        fingerprint,
    )
    assert durable_complete is not None
    assert durable_complete.generation == 5
    assert durable_complete.work.status is WorkUnitStatus.COMPLETE
    assert dispatch_store.unresolved_attempts() == ()
    assert dispatch_store.load_result(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=attempt.lease.fencing_token,
    ) == attempt.result

    dispatch_store.close()
    budget_store.close()
    lease_store.close()
    recursive_store.close()

    reopened_budget = SqliteBudgetStore(db)
    loaded_budget, loaded_generation = reopened_budget.get(
        "m6-hc-transcendence-proof"
    )
    assert loaded_generation == 2
    assert loaded_budget.remaining_active == 0
    assert loaded_budget.remaining_backend_jobs == 0

    reopened_lease = SqliteLeaseStore(db)
    assert (
        reopened_lease.claim(
            work_unit_fingerprint(attempt.work),
            holder="m6-proof-after-restart",
            now=100.0,
            ttl=60.0,
        )
        is None
    )
    reopened_journal = SqliteDispatchAdmissionStore(db)
    assert reopened_journal.unresolved_attempts() == ()
    assert reopened_journal.load_result(
        lineage_id=persisted_budget.lineage_id,
        work_fingerprint_value=fingerprint,
        fencing_token=attempt.lease.fencing_token,
    ) == attempt.result
    reopened_journal.close()

    reopened_recursive = SqliteRecursiveWorkStore(db)
    resumed_work = reopened_recursive.get(
        persisted_budget.lineage_id,
        work_unit_fingerprint(attempt.work),
    )
    assert resumed_work is not None
    assert resumed_work.work == replace(
        attempt.work,
        status=WorkUnitStatus.COMPLETE,
    )
    assert resumed_work.work.status is WorkUnitStatus.COMPLETE
    assert resumed_work.generation == 5
    assert resumed_work.ancestry_fingerprints == {
        work_unit_fingerprint(attempt.work)
    }

    reopened_budget.close()
    reopened_lease.close()
    reopened_recursive.close()
