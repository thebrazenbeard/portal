from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid

from .desktop_ipc import FileBridgeClient


def _wait_until(predicate, *, timeout_seconds: float, poll_seconds: float = 0.1):
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(poll_seconds)
    raise TimeoutError(f"qualification condition timed out; last={last!r}")


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

    human_activity = client.request("desktop_recent_activity", limit=100)
    human_items = human_activity.get("items")
    if not isinstance(human_items, list):
        raise RuntimeError("desktop activity did not return a list")
    human_record = next(
        (
            item
            for item in human_items
            if isinstance(item, dict)
            and item.get("request_id") == human_request_id
        ),
        None,
    )
    if human_record is None:
        raise RuntimeError("human cognition evidence is missing")
    human_acceptance = human_record.get("acceptance")
    if (
        not isinstance(human_acceptance, dict)
        or human_acceptance.get("status") != "ACCEPTED_HOST_OBSERVATION"
        or human_acceptance.get("canonical_memory_write") is not False
        or human_acceptance.get("protected_effect_authority") is not False
    ):
        raise RuntimeError(
            f"human cognition lacks qualified Vera host acceptance: {human_acceptance!r}"
        )

    from pre_active import Store

    store = Store(runtime_root / "state" / "pre-active" / "runtime.sqlite3")
    try:
        autonomous_event_id = store.request_autonomous_turn(
            task="Reply only with PORTAL_DESKTOP_AUTONOMOUS_OK",
            capabilities={"text"},
            source="ENDOGENOUS",
            reason="staged resident autonomous cognition qualification",
            now=time.time(),
            dedup_key="portal-desktop-staged-qualification-v1",
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
        return None

    event_row = _wait_until(
        autonomous_done,
        timeout_seconds=timeout_seconds,
    )

    activity = client.request("desktop_recent_activity", limit=100)
    items = activity.get("items")
    if not isinstance(items, list):
        raise RuntimeError("desktop activity did not return a list")
    autonomous_record = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("request_id") == autonomous_event_id
        ),
        None,
    )
    if autonomous_record is None:
        raise RuntimeError("autonomous cognition evidence is missing")
    if autonomous_record.get("state") != "COMPLETED":
        raise RuntimeError("autonomous cognition evidence is not complete")
    if autonomous_record.get("protected_effect_authority") is not False:
        raise RuntimeError("autonomous cognition claimed protected effect authority")
    autonomous_acceptance = autonomous_record.get("acceptance")
    if (
        not isinstance(autonomous_acceptance, dict)
        or autonomous_acceptance.get("status") != "ACCEPTED_HOST_OBSERVATION"
        or autonomous_acceptance.get("canonical_memory_write") is not False
        or autonomous_acceptance.get("protected_effect_authority") is not False
    ):
        raise RuntimeError(
            "autonomous cognition lacks qualified Vera host acceptance"
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
        return None

    _wait_until(
        volition_done,
        timeout_seconds=timeout_seconds,
    )
    if volition_receipt is None or volition_turn is None:
        raise RuntimeError("Volition qualification evidence is incomplete")

    volition_activity = client.request("desktop_recent_activity", limit=200)
    volition_items = volition_activity.get("items")
    if not isinstance(volition_items, list):
        raise RuntimeError("desktop activity did not return a list")
    volition_record = next(
        (
            item
            for item in volition_items
            if isinstance(item, dict)
            and item.get("request_id")
            == volition_receipt.get("cognition_event_id")
        ),
        None,
    )
    if volition_record is None:
        raise RuntimeError("Volition cognition activity is missing")
    volition_acceptance = volition_record.get("acceptance")
    if (
        volition_record.get("state") != "COMPLETED"
        or not isinstance(volition_acceptance, dict)
        or volition_acceptance.get("status") != "ACCEPTED_HOST_OBSERVATION"
        or volition_acceptance.get("protected_effect_authority") is not False
        or volition_record.get("protected_effect_authority") is not False
    ):
        raise RuntimeError(
            "Volition cognition lacks qualified Vera host acceptance"
        )

    final_status = client.request("desktop_status")
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
            "event_id": autonomous_event_id,
            "event_status": event_row.get("status"),
            "route_id": autonomous_record.get("route_id"),
            "evidence_id": autonomous_record.get("evidence_id"),
        },
        "volition_chain": {
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
        "protected_effect_authority": False,
        "native_openai_router_replaced": False,
        "claim_ceiling": (
            "STAGED_RESIDENT_RUNTIME_QUALIFIED_FOR_LOCAL_COGNITION_AND_DESKTOP_IPC_"
            "NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY"
        ),
        "qualified_at": time.time(),
    }
    output = runtime_root / "QUALIFICATION.json"
    output.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
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
