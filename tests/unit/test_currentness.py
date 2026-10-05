from runner.currentness import subject_changed
from runner.models import EvidenceClass, ExactSubject, Observation


def observation(commit: str) -> Observation:
    return Observation(
        target="provider",
        evidence_class=EvidenceClass.AUTHORITATIVE,
        subject=ExactSubject(repository="org/provider", ref="main", commit=commit),
        observed_value=commit,
        observed_at="2026-09-17T20:40:00Z",
        observer="project-runner/0.1.0",
    )


def test_subject_changed_is_false_for_same_exact_observation():
    current = observation("a" * 40)
    assert not subject_changed(current, current)


def test_subject_changed_is_true_when_commit_moves():
    previous = observation("a" * 40)
    current = observation("b" * 40)
    assert subject_changed(previous, current)


def test_subject_changed_is_true_without_previous_observation():
    assert subject_changed(None, observation("a" * 40))
