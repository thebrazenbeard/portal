import pytest
from jsonschema import ValidationError
from runner.schema import validate_document


def test_worker_schema_rejects_missing_id():
    with pytest.raises(ValidationError):
        validate_document("worker", {"workers": [{"name": "missing id"}]})


def test_project_schema_accepts_minimal_project():
    validate_document("project", {
        "projects": [{
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": ["thebrazenbeard/project-runner"],
            "capabilities": ["read", "analyze", "propose", "write_branch"]
        }]
    })


def test_work_unit_schema_accepts_semantic_work_record():
    validate_document("work-unit", {
        "id": "work-1",
        "root_frontier_id": "frontier-1",
        "parent_work_id": None,
        "inputs": [{
            "repository": "thebrazenbeard/project-runner",
            "ref": "main",
            "commit": "a" * 40,
            "path": None,
            "digest": None,
        }],
        "operation": "REREVIEW",
        "required_capabilities": ["analyze"],
        "collision_keys": ["project:project-runner"],
        "recursion_depth": 0,
        "budget_allocation": {
            "children": 1,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        "expected_outputs": ["backend-result"],
        "completion_criteria": ["exact-subject-current"],
        "status": "PENDING",
    })


def test_work_unit_schema_rejects_negative_depth():
    payload = {
        "id": "work-1",
        "root_frontier_id": "frontier-1",
        "parent_work_id": None,
        "inputs": [{
            "repository": "thebrazenbeard/project-runner",
            "ref": "main",
        }],
        "operation": "REREVIEW",
        "required_capabilities": [],
        "collision_keys": [],
        "recursion_depth": -1,
        "budget_allocation": {},
        "expected_outputs": [],
        "completion_criteria": [],
        "status": "PENDING",
    }
    with pytest.raises(ValidationError):
        validate_document("work-unit", payload)
