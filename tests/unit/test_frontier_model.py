import pytest

from runner.models import Frontier, FrontierStatus


def _payload():
    return {
        "id": "f-1",
        "project": "vera",
        "subject": {
            "repository": "thebrazenbeard/vera",
            "ref": "main",
            "commit": "abc123",
        },
        "work_type": "REREVIEW",
        "reason": "provider moved",
        "dependencies": [],
        "required_capabilities": ["analyze"],
        "collision_keys": ["repo:thebrazenbeard/vera"],
        "cost_class": "SMALL",
        "priority_inputs": {"fanout": 1},
        "status": "READY",
    }


def test_frontier_round_trip_model():
    frontier = Frontier.from_mapping(_payload())
    assert frontier.status is FrontierStatus.READY
    assert frontier.required_capabilities == ("analyze",)
    assert frontier.subject.commit == "abc123"


def test_frontier_missing_required_capabilities_fails_closed():
    payload = _payload()
    del payload["required_capabilities"]
    with pytest.raises((KeyError, ValueError)):
        Frontier.from_mapping(payload)


def test_frontier_status_includes_waiting_authority():
    assert FrontierStatus.WAITING_AUTHORITY.value == "WAITING_AUTHORITY"
