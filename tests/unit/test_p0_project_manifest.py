import copy

import pytest
from jsonschema.exceptions import ValidationError

from runner.schema import validate_document


def _manifest() -> dict:
    return {
        "schema": "PROJECT_RUNNER_P0_PROJECT_MANIFEST_V1",
        "corpus_id": "PROJECT_RUNNER_PORTFOLIO_CORPUS_V1",
        "classification": {
            "project_id": "example",
            "repository": "owner/example",
            "priority": "P0",
            "family_id": "portfolio-spine",
            "activity_state": "ACTIVE",
            "observed_at": "2026-09-24T13:08:00-04:00",
            "source_kind": "PUBLIC_CORPUS_SNAPSHOT",
            "source_subject": "owner/corpus@0123456789012345678901234567890123456789:path",
        },
        "role": {
            "purpose": "Example P0 role.",
            "status": "Active.",
            "current_frontier": "Close one bounded frontier.",
        },
        "governance": {
            "priority_is_authority": False,
            "scheduling_requires_currentness": True,
            "effect_requires_explicit_authority": True,
            "merge_deploy_not_granted": True,
        },
        "build_tracks": [
            {
                "id": "bounded-frontier",
                "goal": "Implement one bounded frontier.",
                "acceptance": ["Exact subject is recorded.", "Relevant tests pass."],
            }
        ],
    }


def test_p0_project_manifest_accepts_governed_manifest() -> None:
    validate_document("p0-project-manifest", _manifest())


def test_p0_project_manifest_rejects_non_p0_priority() -> None:
    payload = copy.deepcopy(_manifest())
    payload["classification"]["priority"] = "P1"
    with pytest.raises(ValidationError):
        validate_document("p0-project-manifest", payload)


def test_p0_project_manifest_rejects_priority_as_authority() -> None:
    payload = copy.deepcopy(_manifest())
    payload["governance"]["priority_is_authority"] = True
    with pytest.raises(ValidationError):
        validate_document("p0-project-manifest", payload)
