import pytest

from runner.models import EvidenceClass, ExactSubject, Observation


def test_exact_subject_identity_changes_with_commit():
    old = ExactSubject(repository="thebrazenbeard/project-runner", ref="main", commit="a" * 40)
    new = ExactSubject(repository="thebrazenbeard/project-runner", ref="main", commit="b" * 40)

    assert old.identity() != new.identity()


def test_observation_from_mapping_rejects_missing_exact_subject():
    with pytest.raises(ValueError, match="subject"):
        Observation.from_mapping({
            "target": "project-runner",
            "evidence_class": "AUTHORITATIVE",
            "observed_value": "abc",
            "observed_at": "2026-09-17T20:30:00Z",
            "observer": "project-runner/0.1.0",
        })


def test_observation_parses_authoritative_exact_subject():
    observation = Observation.from_mapping({
        "target": "project-runner",
        "evidence_class": "AUTHORITATIVE",
        "subject": {
            "repository": "thebrazenbeard/project-runner",
            "ref": "main",
            "commit": "a" * 40,
        },
        "observed_value": "a" * 40,
        "observed_at": "2026-09-17T20:30:00Z",
        "observer": "project-runner/0.1.0",
    })

    assert observation.evidence_class is EvidenceClass.AUTHORITATIVE
    assert observation.subject.commit == "a" * 40


def test_observation_schema_accepts_exact_subject_document():
    from runner.schema import validate_document

    validate_document("observation", {
        "observations": [{
            "target": "project-runner",
            "evidence_class": "AUTHORITATIVE",
            "subject": {
                "repository": "thebrazenbeard/project-runner",
                "ref": "main",
                "commit": "a" * 40,
            },
            "observed_value": "a" * 40,
            "observed_at": "2026-09-17T20:30:00Z",
            "observer": "project-runner/0.1.0",
        }]
    })
