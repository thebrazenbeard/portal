from runner.models import (
    DependencyEdge,
    DependencyReaction,
    DependencySelector,
    EvidenceClass,
    ExactSubject,
    Observation,
)
from runner.propagate import derive_invalidations


def obs(target: str, repository: str, commit: str, path: str | None = None) -> Observation:
    return Observation(
        target=target,
        evidence_class=EvidenceClass.AUTHORITATIVE,
        subject=ExactSubject(repository=repository, ref="main", commit=commit, path=path),
        observed_value=commit,
        observed_at="2026-09-17T20:40:00Z",
        observer="project-runner/0.1.0",
    )


def test_changed_provider_invalidates_only_matching_consumer():
    matching = DependencyEdge(
        id="bus-routing",
        provider="bus",
        consumer="runner",
        kind="routing-contract",
        selector=DependencySelector(
            repository="org/bus",
            ref="main",
            path_prefix="architecture/contracts/",
        ),
        reaction=DependencyReaction.INSPECT,
        evidence="exact-subject",
    )
    unrelated = DependencyEdge(
        id="other",
        provider="other-provider",
        consumer="other-consumer",
        kind="source",
        selector=DependencySelector(repository="org/other", ref="main"),
        reaction=DependencyReaction.RETEST,
        evidence="exact-subject",
    )
    previous = (
        obs("bus", "org/bus", "a" * 40, "architecture/contracts/ROUTING.json"),
    )
    current = (
        obs("bus", "org/bus", "b" * 40, "architecture/contracts/ROUTING.json"),
    )

    invalidations = derive_invalidations(previous, current, (matching, unrelated))

    assert len(invalidations) == 1
    assert invalidations[0].dependency_id == "bus-routing"
    assert invalidations[0].consumer == "runner"
    assert invalidations[0].reaction is DependencyReaction.INSPECT


def test_unchanged_provider_creates_no_invalidation():
    dependency = DependencyEdge(
        id="dep",
        provider="provider",
        consumer="consumer",
        kind="source",
        selector=DependencySelector(repository="org/provider", ref="main"),
        reaction=DependencyReaction.RETEST,
        evidence="exact-subject",
    )
    current = obs("provider", "org/provider", "a" * 40)

    assert derive_invalidations((current,), (current,), (dependency,)) == ()
