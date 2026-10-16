"""Advisory-only route assessment for Vera Mono's durable task handoff V1.

PORTAL does not own the task, grant effect authority, dispatch, or close it.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from .route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)

REQUEST_SCHEMA = "VERA_PORTAL_TASK_ROUTE_REQUEST_V1"
ASSESSMENT_SCHEMA = "VERA_PORTAL_TASK_ROUTE_ASSESSMENT_V1"
ADVISORY_EFFECT = "NO_PROTECTED_EFFECT"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def _hash(body: dict[str, Any]) -> str:
    """Compatible with Vera Mono canonical_json_bytes for this restricted shape."""
    return hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _identity(value: Any, label: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def validate_vera_task_route_request(request: Mapping[str, Any]) -> dict[str, Any]:
    keys = {
        "schema", "task_id", "task_packet_digest", "task_journal_head",
        "subject", "route", "requested_effect", "request_digest",
    }
    if type(request) is not dict or set(request) != keys:
        raise ValueError("unknown or missing task-route request fields")
    if request["schema"] != REQUEST_SCHEMA or request["requested_effect"] != ADVISORY_EFFECT:
        raise ValueError("unsupported task-route request schema/effect")
    _identity(request["task_id"], "task_id")
    _digest(request["task_packet_digest"], "task_packet_digest")
    _digest(request["task_journal_head"], "task_journal_head")
    _digest(request["request_digest"], "request_digest")
    if type(request["subject"]) is not str or not request["subject"].strip():
        raise ValueError("missing subject")
    route = request["route"]
    if type(route) is not dict or set(route) != {
        "node_id", "target_kind", "target_id", "required_capabilities",
    }:
        raise ValueError("invalid route target")
    for key in ("node_id", "target_kind", "target_id"):
        _identity(route[key], key)
    caps = route["required_capabilities"]
    if type(caps) is not list or not 1 <= len(caps) <= 16 or any(
        type(v) is not str or not _ID.fullmatch(v) for v in caps
    ) or caps != sorted(set(caps)):
        raise ValueError("invalid sorted unique route capabilities")
    body = {key: value for key, value in request.items() if key != "request_digest"}
    if _hash(body) != request["request_digest"]:
        raise ValueError("task-route request digest mismatch")
    return dict(request)


def assess_vera_task_route(
    request: dict[str, Any],
    routes: Iterable[PortalRouteAdvertisement],
) -> dict[str, Any]:
    """Return an advisory route, never a dispatch or authority grant."""
    valid = validate_vera_task_route_request(request)
    route = valid["route"]
    query = PortalRouteRequest(
        subject_kind="VERA_TASK",
        subject_id=valid["task_id"],
        node_id=route["node_id"],
        target_kind=route["target_kind"],
        target_id=route["target_id"],
        required_capabilities=tuple(route["required_capabilities"]),
        required_effect=ADVISORY_EFFECT,
    )
    try:
        binding = resolve_portal_route(query, routes)
    except ValueError as exc:
        if str(exc) != "no qualified execution route":
            raise  # Duplicate identities or other registry faults must be visible.
        result = {
            "state": "HELD",
            "route": None,
            "reason": "NO_QUALIFIED_ADVISORY_ROUTE",
        }
    else:
        result = {
            "state": "ROUTE_PROPOSED",
            "route": {"adapter_id": binding.adapter_id, "route_id": binding.route_id},
            "reason": None,
        }
    body = {
        "schema": ASSESSMENT_SCHEMA,
        "task_id": valid["task_id"],
        "task_packet_digest": valid["task_packet_digest"],
        "task_journal_head": valid["task_journal_head"],
        "request_digest": valid["request_digest"],
        **result,
    }
    return {**body, "assessment_digest": _hash(body)}
