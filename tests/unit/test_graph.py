from runner.graph import affected_edges, selector_matches
from runner.models import (
    DependencyEdge,
    DependencyReaction,
    DependencySelector,
    ExactSubject,
)


def edge(edge_id: str, selector: DependencySelector) -> DependencyEdge:
    return DependencyEdge(
        id=edge_id,
        provider="provider",
        consumer="consumer",
        kind="source",
        selector=selector,
        reaction=DependencyReaction.RETEST,
        evidence="exact-subject",
    )


def test_selector_rejects_repository_mismatch():
    selector = DependencySelector(repository="org/provider")
    subject = ExactSubject(repository="org/other", ref="main", commit="a" * 40)

    assert not selector_matches(selector, subject)


def test_selector_rejects_ref_mismatch():
    selector = DependencySelector(repository="org/provider", ref="main")
    subject = ExactSubject(repository="org/provider", ref="release", commit="a" * 40)

    assert not selector_matches(selector, subject)


def test_selector_matches_path_prefix():
    selector = DependencySelector(
        repository="org/provider",
        ref="main",
        path_prefix="architecture/contracts/",
    )
    subject = ExactSubject(
        repository="org/provider",
        ref="main",
        commit="a" * 40,
        path="architecture/contracts/ROUTING.json",
    )

    assert selector_matches(selector, subject)


def test_affected_edges_returns_only_intersections():
    matching = edge(
        "matching",
        DependencySelector(repository="org/provider", ref="main"),
    )
    unrelated = edge(
        "unrelated",
        DependencySelector(repository="org/other", ref="main"),
    )
    subject = ExactSubject(repository="org/provider", ref="main", commit="b" * 40)

    assert affected_edges((matching, unrelated), subject) == (matching,)
