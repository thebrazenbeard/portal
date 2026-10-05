from runner.backends import MockBackend
from runner.budgets import BudgetEnvelope
from runner.dispatch import dispatch_ready
from runner.leases import InMemoryLeaseStore
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus
from runner.verify import verify_attempt
from runner.work_units import WorkUnitStatus


def _frontier(commit="a" * 40):
    return Frontier(
        id="frontier-1",
        project="vera",
        subject=ExactSubject(
            repository="thebrazenbeard/vera",
            ref="main",
            commit=commit,
        ),
        work_type="REREVIEW",
        reason="test",
        dependencies=("dep-1",),
        required_capabilities=("analyze",),
        collision_keys=("project:vera",),
        cost_class=CostClass.SMALL,
        priority_inputs={"fanout": 1, "staleness_risk": 1, "cost": 1, "executable_now": 1},
        status=FrontierStatus.READY,
    )


def _budget():
    return BudgetEnvelope(
        lineage_id="run-1",
        max_depth=3,
        depth=0,
        remaining_children=2,
        remaining_active=2,
        remaining_retries=1,
        remaining_backend_jobs=2,
    )


def _attempt(store):
    batch = dispatch_ready(
        (_frontier(),),
        lease_store=store,
        backend=MockBackend(),
        budget=_budget(),
        holder="runner-a",
        now=100.0,
        lease_ttl=30.0,
    )
    return batch.attempts[0]


def test_stale_output_never_becomes_complete():
    store = InMemoryLeaseStore()
    attempt = _attempt(store)
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=101.0,
        current_subject_reader=lambda _: ExactSubject(
            repository="thebrazenbeard/vera",
            ref="main",
            commit="b" * 40,
        ),
        evidence_verifier=lambda work, result: True,
    )
    assert outcome.status is WorkUnitStatus.SUPERSEDED


def test_unchanged_exact_subject_and_independent_evidence_can_complete():
    store = InMemoryLeaseStore()
    attempt = _attempt(store)
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=101.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: True,
    )
    assert outcome.status is WorkUnitStatus.COMPLETE
    assert store.claim(
        attempt.result.work_fingerprint,
        holder="runner-b",
        now=200.0,
        ttl=20.0,
    ) is None


def test_worker_success_claim_alone_is_insufficient():
    store = InMemoryLeaseStore()
    attempt = _attempt(store)
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=101.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: False,
    )
    assert outcome.status is WorkUnitStatus.VERIFYING


def test_unavailable_currentness_is_outcome_unknown():
    store = InMemoryLeaseStore()
    attempt = _attempt(store)
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=101.0,
        current_subject_reader=lambda subject: None,
        evidence_verifier=lambda work, result: True,
    )
    assert outcome.status is WorkUnitStatus.OUTCOME_UNKNOWN
