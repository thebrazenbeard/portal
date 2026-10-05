import pytest
from runner.budgets import BudgetEnvelope
from runner.dispatch import dispatch_ready
from runner.github_backend import (
    GitHubBackend,
    GitHubOperation,
    TargetAuthorityGrant,
)
from runner.leases import InMemoryLeaseStore
from runner.m6_github import (
    GitHubCurrentSubjectReader,
    GitHubReadInspectionBackend,
    frontier_to_github_inspection_work,
)
from runner.models import CostClass, ExactSubject, Frontier, FrontierStatus
from runner.verify import verify_attempt
from runner.work_units import WorkUnitStatus, work_unit_fingerprint


HC_HEAD = "618245b54fb923c7a204892c6953ab6d1c5dac57"
TRANSCENDENCE_HEAD = "ca3965c5788cd1bbb52fc527e47e3bcd5eca6e4a"


class FakeReadTransport:
    def __init__(self):
        self.heads = {
            ("thebrazenbeard/hc-brain", "main"): HC_HEAD,
            (
                "thebrazenbeard/transcendence",
                "architecture/consciousness-backup-v1",
            ): TRANSCENDENCE_HEAD,
        }
        self.calls = []

    def read_ref(self, repository, ref):
        self.calls.append((repository, ref))
        return self.heads[(repository, ref)]


def _frontier():
    return Frontier(
        id="frontier-hc-transcendence",
        project="transcendence",
        subject=ExactSubject(
            repository="thebrazenbeard/hc-brain",
            ref="main",
            commit=HC_HEAD,
        ),
        work_type="INSPECT",
        reason="HC reference substrate moved",
        dependencies=("hc-brain-to-transcendence-reference-inspection",),
        required_capabilities=("analyze",),
        collision_keys=("project:transcendence",),
        cost_class=CostClass.SMALL,
        priority_inputs={
            "criticality": 1,
            "dependency_depth": 1,
            "staleness": 1,
            "uncertainty": 1,
            "cost": 1,
        },
        status=FrontierStatus.READY,
    )


def _target():
    return ExactSubject(
        repository="thebrazenbeard/transcendence",
        ref="architecture/consciousness-backup-v1",
        commit=TRANSCENDENCE_HEAD,
    )


def _budget():
    return BudgetEnvelope(
        lineage_id="m6-live-read",
        max_depth=1,
        depth=0,
        remaining_children=0,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
    )


def _grant(repository, ref):
    return TargetAuthorityGrant(
        repository=repository,
        operations=(GitHubOperation.READ_REF,),
        ref_prefixes=(ref,),
    )


def _backend(transport, *, include_target=True):
    grants = [
        _grant("thebrazenbeard/hc-brain", "main"),
    ]
    if include_target:
        grants.append(
            _grant(
                "thebrazenbeard/transcendence",
                "architecture/consciousness-backup-v1",
            )
        )
    return GitHubBackend(
        transport=transport,
        route_capabilities=("github.read_ref",),
        grants=tuple(grants),
    )


def test_m6_read_adapter_requires_explicit_grants_for_provider_and_target():
    transport = FakeReadTransport()
    backend = GitHubReadInspectionBackend(
        _backend(transport, include_target=False)
    )
    target = _target()
    batch = dispatch_ready(
        (_frontier(),),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(),
        holder="m6-authority-test",
        now=0.0,
        lease_ttl=30.0,
        work_factory=lambda frontier, depth: frontier_to_github_inspection_work(
            frontier,
            target,
            depth,
        ),
    )

    assert len(batch.attempts) == 1
    result = batch.attempts[0].result
    assert result.succeeded is False
    assert result.classification == "AUTHORITY_DENIED"
    joined = " ".join(result.outputs + result.evidence)
    assert "transcendence" not in joined
    assert "thebrazenbeard" not in joined
    assert TRANSCENDENCE_HEAD not in joined


def test_m6_read_adapter_checks_both_exact_heads_and_redacts_result_metadata():
    transport = FakeReadTransport()
    backend = GitHubReadInspectionBackend(_backend(transport))
    target = _target()
    batch = dispatch_ready(
        (_frontier(),),
        lease_store=InMemoryLeaseStore(),
        backend=backend,
        budget=_budget(),
        holder="m6-success-test",
        now=0.0,
        lease_ttl=30.0,
        work_factory=lambda frontier, depth: frontier_to_github_inspection_work(
            frontier,
            target,
            depth,
        ),
    )

    assert len(batch.attempts) == 1
    result = batch.attempts[0].result
    assert result.succeeded is True
    assert result.outputs == ("READ_REF_OK", "READ_REF_OK")
    assert result.evidence == (
        "m6:github-read-inspection",
        "github:readback-verified",
        "subjects=2",
    )
    joined = " ".join(result.outputs + result.evidence)
    assert "hc-brain" not in joined
    assert "transcendence" not in joined
    assert HC_HEAD not in joined
    assert TRANSCENDENCE_HEAD not in joined
    assert transport.calls == [
        ("thebrazenbeard/hc-brain", "main"),
        (
            "thebrazenbeard/transcendence",
            "architecture/consciousness-backup-v1",
        ),
    ]


def test_independent_subject_reader_uses_separate_authority_gated_read():
    transport = FakeReadTransport()
    reader = GitHubCurrentSubjectReader(_backend(transport))

    observed = reader.read(_target())

    assert observed == _target()
    assert transport.calls == [
        (
            "thebrazenbeard/transcendence",
            "architecture/consciousness-backup-v1",
        )
    ]


def test_registry_digest_is_part_of_m6_work_semantic_identity():
    first = frontier_to_github_inspection_work(
        _frontier(),
        _target(),
        registry_digest="a" * 64,
    )
    second = frontier_to_github_inspection_work(
        _frontier(),
        _target(),
        registry_digest="b" * 64,
    )

    assert first.payload["m6"]["project_registry_sha256"] == "a" * 64
    assert work_unit_fingerprint(first) != work_unit_fingerprint(second)


def test_registry_digest_rejects_non_sha256_tokens():
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        frontier_to_github_inspection_work(
            _frontier(),
            _target(),
            registry_digest="PRIVATE-REGISTRY-NAME",
        )


def test_live_read_precondition_drift_is_superseded_not_failed():
    transport = FakeReadTransport()
    backend = GitHubReadInspectionBackend(_backend(transport))
    target = _target()
    transport.heads[
        (
            "thebrazenbeard/transcendence",
            "architecture/consciousness-backup-v1",
        )
    ] = "f" * 40
    store = InMemoryLeaseStore()
    batch = dispatch_ready(
        (_frontier(),),
        lease_store=store,
        backend=backend,
        budget=_budget(),
        holder="m6-stale-precondition",
        now=0.0,
        lease_ttl=30.0,
        work_factory=lambda frontier, depth: frontier_to_github_inspection_work(
            frontier,
            target,
            depth,
        ),
    )

    attempt = batch.attempts[0]
    assert attempt.result.classification == "PRECONDITION_FAILED"
    outcome = verify_attempt(
        attempt,
        lease_store=store,
        now=1.0,
        current_subject_reader=lambda subject: subject,
        evidence_verifier=lambda work, result: False,
    )

    assert outcome.status is WorkUnitStatus.SUPERSEDED
    assert outcome.reason == "backend precondition no longer matches exact subject"
