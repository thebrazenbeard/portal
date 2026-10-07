from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
import uuid

from .desktop_ipc import FileBridgeClient


def _qualified_portfolio_telemetry(portfolio: object) -> str:
    """Qualify read-only portfolio telemetry, not worker execution or authority."""
    error = "portfolio status IPC did not return governed telemetry"
    if not isinstance(portfolio, dict):
        raise RuntimeError(error)
    schema = portfolio.get("schema")
    mode = portfolio.get("dispatch_mode")
    if (
        schema not in {
            "PORTAL_DESKTOP_PORTFOLIO_V1",
            "PORTAL_DESKTOP_PORTFOLIO_V2",
        }
        or portfolio.get("protected_effect_authority") is not False
        or not isinstance(portfolio.get("sessions"), list)
        or type(portfolio.get("configured")) is not bool
    ):
        raise RuntimeError(error)

    if schema == "PORTAL_DESKTOP_PORTFOLIO_V1":
        if mode != "ADMISSION_ONLY":
            raise RuntimeError(error)
        return mode

    worker_state = portfolio.get("worker_state")
    if mode == "ADMISSION_ONLY":
        if worker_state != "UNAVAILABLE":
            raise RuntimeError(error)
    elif mode == "PROCESS_PROPOSAL":
        profile = portfolio.get("profile")
        if (
            worker_state != "CONFIGURED"
            or portfolio.get("configured") is not True
            or not isinstance(profile, dict)
            or not isinstance(profile.get("worker_backends"), str)
            or not profile["worker_backends"].strip()
        ):
            raise RuntimeError(error)
    else:
        raise RuntimeError(error)

    # A status response can describe a configured worker, but never prove
    # that it ran, verified a result, or has protected-effect authority.
    return mode


def _wait_until(predicate, *, timeout_seconds: float, poll_seconds: float = 0.1, label: str = "qualification condition"):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("qualification timeout must be finite and positive")
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(poll_seconds)
    raise TimeoutError(f"{label} timed out; last={last!r}")


def _qualified_acceptance(record: dict[str, object], *, label: str, runtime_id: str) -> dict[str, object]:
    receipt = record.get("acceptance")
    if (record.get("state") != "COMPLETED"
            or record.get("protected_effect_authority") is not False
            or not record.get("evidence_id")
            or not isinstance(receipt, dict)
            or receipt.get("status") != "ACCEPTED_HOST_OBSERVATION"
            or receipt.get("canonical_memory_write") is not False
            or receipt.get("protected_effect_authority") is not False
            or receipt.get("request_id") != record.get("request_id")
            or receipt.get("runtime_id") != runtime_id
            or receipt.get("route_id") != record.get("route_id")):
        raise RuntimeError(f"{label} lacks exact qualified Vera host acceptance")
    return receipt


def _wait_cognition(client, *, runtime_id: str, label: str, timeout_seconds: float,
                    source_event_id: str | None = None, request_id: str | None = None,
                    source: str = "PRE_ACTIVE_AUTONOMOUS_TURN") -> dict[str, object]:
    if (source_event_id is None) == (request_id is None):
        raise ValueError("qualification must correlate one exact source event or request")
    def completed():
        payload = client.request("desktop_recent_activity", limit=200)
        items = payload.get("items")
        if not isinstance(items, list):
            raise RuntimeError("desktop activity did not return a list")
        for record in items:
            if (isinstance(record, dict)
                    and ((record.get("source_event_id") == source_event_id) if source_event_id is not None else record.get("request_id") == request_id)
                    and record.get("source") == source
                    and record.get("state") == "COMPLETED"):
                _qualified_acceptance(record, label=label, runtime_id=runtime_id)
                return record
        return None
    return _wait_until(completed, timeout_seconds=timeout_seconds, label=label)


def qualify_runtime(
    runtime_root: Path,
    *,
    timeout_seconds: float = 120.0,
    require_local_route: bool = True,
) -> dict[str, object]:
    runtime_root = Path(runtime_root).resolve()
    client = FileBridgeClient(runtime_root, timeout_seconds=timeout_seconds)

    status = _wait_until(
        lambda: (
            value
            if (value := client.request("desktop_status")).get("state") == "ACTIVE"
            else None
        ),
        timeout_seconds=timeout_seconds,
    )
    runtime_id = status.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id:
        raise RuntimeError("desktop status missing runtime identity")
    components = status.get("components")
    if not isinstance(components, dict):
        raise RuntimeError("desktop status missing components")
    for name in ("vera_mono", "portal", "pre_active", "volition"):
        component = components.get(name)
        if not isinstance(component, dict) or component.get("loaded") is not True:
            raise RuntimeError(f"runtime component not loaded: {name}")
    if status.get("protected_effect_authority") is not False:
        raise RuntimeError("runtime status claimed protected effect authority")

    route_payload = client.request("desktop_discover_routes")
    routes = route_payload.get("routes")
    if not isinstance(routes, list):
        raise RuntimeError("route discovery did not return a list")
    local_routes = [
        route
        for route in routes
        if isinstance(route, dict)
        and route.get("local") is True
        and route.get("available") is True
        and route.get("current") is True
        and route.get("incremental_paid_compute") is False
        and route.get("auto_admissible") is True
        and route.get("effect_authority_ceiling") == "COGNITION_ONLY_NO_PROTECTED_EFFECT"
    ]
    if require_local_route and not local_routes:
        raise RuntimeError("no admissible local no-incremental-paid-compute route")

    human_request_id = "qualification-human-" + uuid.uuid4().hex
    human = client.request(
        "desktop_cognize",
        request_id=human_request_id,
        source="HUMAN",
        reason="staged runtime qualification",
        task="Reply only with PORTAL_DESKTOP_HUMAN_OK",
        created_at=time.time(),
        required_capabilities=["text"],
    )
    if human.get("state") != "COMPLETED":
        raise RuntimeError(f"human cognition did not complete: {human}")
    human_text = str(human.get("response_text") or "").strip()
    if "PORTAL_DESKTOP_HUMAN_OK" not in human_text:
        raise RuntimeError(f"unexpected human cognition response: {human_text!r}")
    if human.get("protected_effect_authority") is not False:
        raise RuntimeError("human cognition claimed protected effect authority")

    # The response and cached activity publish separately; wait for the same
    # completed request to become visible before assessing durable evidence.
    human_record = _wait_cognition(
        client, request_id=human_request_id, source="HUMAN", runtime_id=runtime_id,
        label="human cognition", timeout_seconds=timeout_seconds,
    )
    _qualified_acceptance(human_record, label="human cognition", runtime_id=runtime_id)
    if (human_record.get("source") != "HUMAN"
            or human_record.get("evidence_id") != human.get("evidence_id")
            or human_record.get("route_id") != human.get("route_id")):
        raise RuntimeError("human response does not match its durable cognition provenance")

    from pre_active import Store

    store = Store(runtime_root / "state" / "pre-active" / "runtime.sqlite3")
    try:
        autonomous_event_id = store.request_autonomous_turn(
            task="Reply only with PORTAL_DESKTOP_AUTONOMOUS_OK",
            capabilities={"text"},
            source="ENDOGENOUS",
            reason="staged resident autonomous cognition qualification",
            now=time.time(),
            dedup_key="portal-desktop-staged-qualification-" + uuid.uuid4().hex,
        )
    finally:
        store.close()

    def autonomous_done():
        check = Store(runtime_root / "state" / "pre-active" / "runtime.sqlite3")
        try:
            rows = check.list_events(kind="autonomous.turn")
            row = next(
                (item for item in rows if item.get("id") == autonomous_event_id),
                None,
            )
        finally:
            check.close()
        if row is not None and row.get("status") == "DONE":
            return row
        if row is not None and row.get("status") in {"DEAD", "CANCELLED"}:
            raise RuntimeError("autonomous source event was not admitted")
        return None

    event_row = _wait_until(
        autonomous_done,
        timeout_seconds=timeout_seconds,
        label="autonomous source admission",
    )

    # Pre-Active ACKs the source when it admits a run. Model execution happens
    # in its run.step event, whose cognition request has a different identifier.
    autonomous_record = _wait_cognition(
        client, source_event_id=autonomous_event_id, runtime_id=runtime_id,
        label="autonomous cognition", timeout_seconds=timeout_seconds,
    )

    from pre_active.volition_bridge import VolitionBridge

    volition_store = Store(
        runtime_root / "state" / "pre-active" / "runtime.sqlite3"
    )
    try:
        volition_signal_id = VolitionBridge(volition_store).enqueue_signal(
            payload={
                "target": "qualify-resident-volition-chain",
                "kind": "open_loop",
                "magnitude": 0.8,
                "confidence": 1.0,
                "provenance": "current_observation",
                "source": "qualification:desktop-host",
                "effect_authority": False,
            },
            now=time.time(),
            dedup_key="portal-desktop-volition-qualification-" + uuid.uuid4().hex,
        )
    finally:
        volition_store.close()

    volition_receipt: dict[str, object] | None = None
    volition_turn: dict[str, object] | None = None

    def volition_done():
        nonlocal volition_receipt, volition_turn
        check = Store(
            runtime_root / "state" / "pre-active" / "runtime.sqlite3"
        )
        try:
            receipt = check.get_volition_signal_receipt(volition_signal_id)
            if receipt is None:
                return None
            cognition_event_id = receipt.get("cognition_event_id")
            if not cognition_event_id:
                raise RuntimeError(
                    "Volition signal produced no cognition event during qualification"
                )
            rows = check.list_events(kind="autonomous.turn")
            turn = next(
                (
                    item
                    for item in rows
                    if item.get("id") == cognition_event_id
                ),
                None,
            )
        finally:
            check.close()
        if turn is not None and turn.get("status") == "DONE":
            volition_receipt = dict(receipt)
            volition_turn = dict(turn)
            return turn
        if turn is not None and turn.get("status") in {"DEAD", "CANCELLED"}:
            raise RuntimeError("Volition source event was not admitted")
        return None

    _wait_until(
        volition_done,
        timeout_seconds=timeout_seconds,
        label="Volition source admission",
    )
    if volition_receipt is None or volition_turn is None:
        raise RuntimeError("Volition qualification evidence is incomplete")

    volition_record = _wait_cognition(
        client, source_event_id=str(volition_receipt["cognition_event_id"]),
        runtime_id=runtime_id, label="Volition cognition", timeout_seconds=timeout_seconds,
    )
    volition_acceptance = _qualified_acceptance(
        volition_record, label="Volition cognition", runtime_id=runtime_id,
    )

    # Read-only control-plane proof: no run/continue or worker dispatch occurs.
    portfolio = client.request(
        "desktop_portfolio", action="status", session_id="qualification-" + uuid.uuid4().hex,
    )
    portfolio_dispatch_mode = _qualified_portfolio_telemetry(portfolio)

    final_status = client.request("desktop_status")
    if (final_status.get("runtime_id") != runtime_id
            or final_status.get("source_manifest") != status.get("source_manifest")):
        raise RuntimeError("runtime identity or bound sources changed during qualification")
    if final_status.get("state") != "ACTIVE" or final_status.get("protected_effect_authority") is not False:
        raise RuntimeError("runtime is not healthy at qualification completion")
    if require_local_route:
        admitted_route_ids = {route.get("route_id") for route in local_routes}
        for label, record in (("human", human_record), ("autonomous", autonomous_record), ("Volition", volition_record)):
            if record.get("route_id") not in admitted_route_ids:
                raise RuntimeError(f"{label} cognition did not consume a discovered admissible local route")
    result = {
        "schema": "VERA_DESKTOP_RUNTIME_QUALIFICATION_V1",
        "qualified": True,
        "runtime_root": str(runtime_root),
        "runtime_id": final_status.get("runtime_id"),
        "source_manifest": final_status.get("source_manifest"),
        "components_loaded": True,
        "local_route_count": len(local_routes),
        "human_cognition": {
            "request_id": human_request_id,
            "state": human.get("state"),
            "route_id": human.get("route_id"),
            "evidence_id": human.get("evidence_id"),
        },
        "autonomous_cognition": {
            "request_id": autonomous_record.get("request_id"),
            "event_id": autonomous_event_id,
            "event_status": event_row.get("status"),
            "route_id": autonomous_record.get("route_id"),
            "evidence_id": autonomous_record.get("evidence_id"),
        },
        "volition_chain": {
            "request_id": volition_record.get("request_id"),
            "signal_event_id": volition_signal_id,
            "state_revision": volition_receipt.get("state_revision"),
            "goal_id": volition_receipt.get("goal_id"),
            "choice_class": volition_receipt.get("choice_class"),
            "autonomous_event_id": volition_receipt.get("cognition_event_id"),
            "autonomous_event_status": volition_turn.get("status"),
            "route_id": volition_record.get("route_id"),
            "evidence_id": volition_record.get("evidence_id"),
            "acceptance_status": volition_acceptance.get("status"),
            "effect_authority": False,
        },
        "portfolio_ipc": {
            "status_only": True,
            "dispatch_mode": portfolio_dispatch_mode,
            "configured": portfolio.get("configured"),
            "observed_session_count": len(portfolio["sessions"]),
            "worker_execution_verified": False,
            "protected_effect_authority": False,
        },
        "protected_effect_authority": False,
        "native_openai_router_replaced": False,
        "claim_ceiling": (
            "STAGED_RESIDENT_RUNTIME_QUALIFIED_FOR_LOCAL_COGNITION_AND_DESKTOP_IPC_"
            "NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY"
        ),
        "qualified_at": time.time(),
    }
    output = runtime_root / "QUALIFICATION.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--allow-no-local-route", action="store_true")
    args = parser.parse_args()
    result = qualify_runtime(
        args.runtime_root,
        timeout_seconds=args.timeout_seconds,
        require_local_route=not args.allow_no_local_route,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
