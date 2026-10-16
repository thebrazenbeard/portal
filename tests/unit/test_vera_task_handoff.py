"""Advisory task routing only: no Vera Mono task ownership or effect promotion."""
from copy import deepcopy
import hashlib
import json

import pytest

from portal.route_resolver import PortalRouteAdvertisement
from portal.vera_task_handoff import (
    assess_vera_task_route, validate_vera_task_route_request,
)


def digest(body):
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def request():
    body={
        "schema":"VERA_PORTAL_TASK_ROUTE_REQUEST_V1",
        "task_id":"task-001",
        "task_packet_digest":"a"*64,
        "task_journal_head":"b"*64,
        "subject":"thebrazenbeard/portal@main",
        "route":{
            "node_id":"repo-native",
            "target_kind":"repository",
            "target_id":"thebrazenbeard/portal",
            "required_capabilities":["repository_read"],
        },
        "requested_effect":"NO_PROTECTED_EFFECT",
    }
    return {**body,"request_digest":digest(body)}


def advertised(**changes):
    values=dict(
        adapter_id="github",route_id="read-only",node_id="repo-native",
        target_kind="repository",target_id="thebrazenbeard/portal",
        capabilities=("repository_read",),
        effect_capabilities=("NO_PROTECTED_EFFECT",),
        authorized_effects=("NO_PROTECTED_EFFECT",),
        available=True,attached=True,current=True,
    )
    values.update(changes)
    return PortalRouteAdvertisement(**values)


def test_advisory_route_selected_and_exact_digest_bound():
    req=request()
    result=assess_vera_task_route(req,[advertised()])
    assert result["state"]=="ROUTE_PROPOSED"
    assert result["route"]=={"adapter_id":"github","route_id":"read-only"}
    assert result["reason"] is None
    assert result["request_digest"]==req["request_digest"]
    assert result["task_packet_digest"]==req["task_packet_digest"]
    assert result["task_journal_head"]==req["task_journal_head"]
    assert result["assessment_digest"]==digest({k:v for k,v in result.items() if k!="assessment_digest"})
    assert "permit" not in result and "verified" not in result
    assert assess_vera_task_route(req,[advertised()])==result


@pytest.mark.parametrize("attr",["available","attached","current"])
def test_unavailable_or_stale_advertisement_stays_held(attr):
    result=assess_vera_task_route(request(),[advertised(**{attr:False})])
    assert result["state"]=="HELD"
    assert result["reason"]=="NO_QUALIFIED_ADVISORY_ROUTE"
    assert result["route"] is None


def test_write_only_route_does_not_authorize_advisory_handoff():
    result=assess_vera_task_route(request(),[advertised(
        effect_capabilities=("SOURCE_WRITE",),
        authorized_effects=("SOURCE_WRITE",),
    )])
    assert result["state"]=="HELD"


def test_tampered_or_unsupported_request_fails_closed():
    req=request()
    changed=deepcopy(req)
    changed["route"]["target_id"]="other/repo"
    with pytest.raises(ValueError,match="digest"):
        validate_vera_task_route_request(changed)
    changed=deepcopy(req)
    changed["requested_effect"]="SOURCE_WRITE"
    changed["request_digest"]=digest({k:v for k,v in changed.items() if k!="request_digest"})
    with pytest.raises(ValueError,match="unsupported"):
        assess_vera_task_route(changed,[advertised()])
    changed=deepcopy(req)
    changed["route"]["required_capabilities"]=["repository_read","repository_read"]
    changed["request_digest"]=digest({k:v for k,v in changed.items() if k!="request_digest"})
    with pytest.raises(ValueError,match="sorted unique"):
        assess_vera_task_route(changed,[advertised()])
    changed=deepcopy(req)
    changed["unauthorized"]=True
    with pytest.raises(ValueError,match="unknown"):
        assess_vera_task_route(changed,[advertised()])


def test_duplicate_advertisements_raise_registry_error_not_held():
    with pytest.raises(ValueError,match="duplicate"):
        assess_vera_task_route(request(),[advertised(),advertised()])


def test_route_assessment_is_not_execution_and_returns_no_execution_receipt():
    advisory=assess_vera_task_route(request(),[advertised()])
    assert set(advisory)=={
        "schema","task_id","task_packet_digest","task_journal_head",
        "request_digest","state","route","reason","assessment_digest",
    }
    assert "dispatch_id" not in advisory
