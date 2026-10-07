from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

from .desktop_cognition import discover_pre_active_target_routes
from .desktop_ipc import DesktopRuntimeCommandHandler
from .desktop_pre_active import (
    process_one_autonomous_turn,
    process_one_volition_signal,
)
from .desktop_runtime import CognitionLedger, ResidentCognitionEngine
from .desktop_vera_acceptance import VeraRuntimeAcceptance


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _runtime_id(source_manifest: dict[str, object]) -> str:
    sources = source_manifest.get("sources", [])
    if "cognition_target" in source_manifest:
        identity_payload: object = {
            "sources": sources,
            "cognition_target": source_manifest.get("cognition_target"),
        }
    else:
        # Preserve the V1 runtime identity derivation for historical installs.
        identity_payload = sources
    raw = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _component_status(
    *,
    vera: object,
    portal: object,
    pre_active: object,
    volition_snapshot: dict[str, object],
    source_manifest: dict[str, object],
    runtime_id: str,
    pid: int,
    started_at: float,
    last_cognition: dict[str, object] | None,
) -> dict[str, object]:
    try:
        vera_context = vera.resume_context()
    except Exception as exc:
        vera_context = {
            "restart_status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        pre_snapshot = pre_active.operational_snapshot(now=time.time())
    except Exception as exc:
        pre_snapshot = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "schema": "VERA_DESKTOP_RUNTIME_STATUS_V1",
        "runtime_id": runtime_id,
        "pid": pid,
        "observed_at": time.time(),
        "started_at": started_at,
        "uptime_seconds": max(0.0, time.time() - started_at),
        "state": "ACTIVE",
        "components": {
            "vera_mono": {
                "loaded": True,
                "runtime_class": type(vera).__name__,
                "restart_status": (
                    vera_context.get("restart_status")
                    if isinstance(vera_context, dict)
                    else "UNKNOWN"
                ),
                "module": sys.modules.get("vera_core").__file__
                if sys.modules.get("vera_core")
                else None,
            },
            "portal": {
                "loaded": True,
                "runtime_class": type(portal).__name__,
                "module": __file__,
            },
            "pre_active": {
                "loaded": True,
                "runtime_class": type(pre_active).__name__,
                "operational_snapshot": pre_snapshot,
                "scheduler_resident": True,
                "observers_resident": True,
            },
            "volition": {
                "loaded": True,
                "runtime_class": "VolitionBridge",
                "snapshot": volition_snapshot,
            },
        },
        "source_manifest": source_manifest,
        "last_cognition": last_cognition,
        "protected_effect_authority": False,
        "native_openai_router_replaced": False,
    }


def prepare_runtime_layout(runtime_root: Path) -> dict[str, Path]:
    runtime_root = Path(runtime_root).resolve()
    state_root = runtime_root / "state"
    bridge_root = runtime_root / "bridge"
    requests = bridge_root / "requests"
    claimed = bridge_root / "claimed"
    responses = bridge_root / "responses"
    for path in (
        state_root,
        state_root / "vera",
        state_root / "portal",
        state_root / "pre-active",
        state_root / "cognition",
        requests,
        claimed,
        responses,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "runtime_root": runtime_root,
        "state_root": state_root,
        "bridge_root": bridge_root,
        "requests": requests,
        "claimed": claimed,
        "responses": responses,
    }


def claim_bridge_request(
    request_path: Path,
    claimed_dir: Path,
) -> Path | None:
    claimed_path = claimed_dir / request_path.name
    if claimed_path.exists():
        raise FileExistsError(
            f"bridge request already has a durable claim: {request_path.stem}"
        )
    try:
        request_path.rename(claimed_path)
    except FileNotFoundError:
        return None
    return claimed_path


def discover_resident_cognition_routes(
    pre_active: object,
    *,
    models_probe=None,
):
    active_target_probe = getattr(pre_active, "get_active_model_target", None)
    if not callable(active_target_probe):
        return ()
    return discover_pre_active_target_routes(
        active_target_probe=active_target_probe,
        models_probe=models_probe,
    )


def run_host(runtime_root: Path) -> int:
    layout = prepare_runtime_layout(runtime_root)
    runtime_root = layout["runtime_root"]
    state_root = layout["state_root"]
    bridge_root = layout["bridge_root"]
    requests = layout["requests"]
    claimed = layout["claimed"]
    responses = layout["responses"]
    heartbeat = bridge_root / "heartbeat.json"
    pid_file = bridge_root / "pid.txt"
    install_spec = runtime_root / "RUNTIME_INSTALL_SPEC.json"

    from vera_core import QualifiedVeraRuntime, VeraStateDirectory
    from portal import PortalCommandSession
    from pre_active import Store
    from pre_active.observers import ObserverManager
    from pre_active.scheduler import Scheduler

    for path in (state_root, requests, responses):
        path.mkdir(parents=True, exist_ok=True)

    if not install_spec.is_file():
        raise RuntimeError("RUNTIME_INSTALL_SPEC.json is required")
    source_manifest = json.loads(install_spec.read_text(encoding="utf-8-sig"))
    if not isinstance(source_manifest, dict):
        raise RuntimeError("runtime install spec must be an object")
    runtime_id = _runtime_id(source_manifest)

    if os.name == "nt":
        import ctypes

        mutex_name = "Local\\VeraDesktopRuntime_" + runtime_id[:24]
        mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if not mutex_handle:
            raise OSError("failed to create Vera desktop runtime mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:
            raise SystemExit("Vera desktop runtime is already running")
    else:
        mutex_handle = None

    vera_state = VeraStateDirectory(
        state_root / "vera",
        project_id="VERA_MONO",
        identity_id="VERA_PROJECT_IDENTITY_V1",
    )
    vera = QualifiedVeraRuntime.from_state_directory(vera_state)
    portal_session = PortalCommandSession(state_root / "portal" / "session.sqlite3")
    pre_active = Store(state_root / "pre-active" / "runtime.sqlite3")
    scheduler = Scheduler(pre_active)
    observers = ObserverManager(pre_active)
    cognition_ledger = CognitionLedger(
        state_root / "cognition" / "desktop-cognition.sqlite3"
    )
    def resident_routes():
        return discover_resident_cognition_routes(pre_active)

    cognition_engine = ResidentCognitionEngine(
        ledger=cognition_ledger,
        discover_routes=resident_routes,
        accept_result=VeraRuntimeAcceptance(
            vera,
            runtime_id=runtime_id,
        ),
    )

    started_at = time.time()
    running = True
    last_cognition: dict[str, object] | None = None

    def volition_snapshot() -> dict[str, object]:
        current = pre_active.get_volition_state()
        if current is None:
            return {
                "state_revision": 0,
                "active_goal": None,
                "effect_authority": False,
            }
        snapshot = current.get("snapshot", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        return {
            "state_revision": int(current.get("revision", 0)),
            "active_goal": snapshot.get("active_goal"),
            "effect_authority": False,
        }

    def status() -> dict[str, object]:
        return _component_status(
            vera=vera,
            portal=portal_session,
            pre_active=pre_active,
            volition_snapshot=volition_snapshot(),
            source_manifest=source_manifest,
            runtime_id=runtime_id,
            pid=os.getpid(),
            started_at=started_at,
            last_cognition=last_cognition,
        )

    handler = DesktopRuntimeCommandHandler(
        engine=cognition_engine,
        discover_routes=resident_routes,
        runtime_status=status,
    )

    def handle(payload: dict[str, object]) -> dict[str, object]:
        nonlocal running
        command = payload.get("command")
        if command in {
            "desktop_status",
            "desktop_discover_routes",
            "desktop_recent_activity",
            "desktop_cognize",
        }:
            return handler.handle(payload)
        if command in {"status", "ping"}:
            return status()
        if command == "pre_active_snapshot":
            return pre_active.operational_snapshot(now=time.time())
        if command == "vera_context":
            value = vera.resume_context()
            if not isinstance(value, dict):
                raise RuntimeError("Vera resume context is not an object")
            return value
        if command == "portal_status":
            return portal_session.status(str(payload["session_id"]))
        if command == "shutdown":
            running = False
            return {
                "stopping": True,
                "runtime_id": runtime_id,
                "protected_effect_authority": False,
            }
        raise ValueError(f"unsupported command: {command!r}")

    def write_heartbeat() -> None:
        _atomic_json(heartbeat, status())
        pid_file.write_text(str(os.getpid()) + "\n", encoding="utf-8")

    try:
        write_heartbeat()
        last_heartbeat = 0.0
        while running:
            now = time.time()
            if now - last_heartbeat >= 1.0:
                observers.tick(now=now)
                scheduler.tick(now=now)
                try:
                    process_one_volition_signal(pre_active, now=now)
                except Exception:
                    pass
                try:
                    result = process_one_autonomous_turn(
                        pre_active,
                        cognition_engine,
                        now=now,
                    )
                    if result is not None:
                        last_cognition = {
                            "request_id": result.request_id,
                            "state": result.state,
                            "route_id": result.route_id,
                            "evidence_id": result.evidence_id,
                            "observed_at": now,
                            "protected_effect_authority": False,
                        }
                except Exception:
                    pass
                write_heartbeat()
                last_heartbeat = now

            claimed_paths = list(sorted(claimed.glob("*.json")))
            for request_path in sorted(requests.glob("*.json")):
                try:
                    claimed_path = claim_bridge_request(
                        request_path,
                        claimed,
                    )
                except FileExistsError:
                    request_path.unlink(missing_ok=True)
                    continue
                if claimed_path is not None:
                    claimed_paths.append(claimed_path)

            for claimed_path in claimed_paths:
                request_id = claimed_path.stem
                try:
                    payload = json.loads(
                        claimed_path.read_text(
                            encoding="utf-8-sig"
                        ).lstrip("\ufeff")
                    )
                    if not isinstance(payload, dict):
                        raise ValueError("request payload must be an object")
                    payload_request_id = str(payload["request_id"])
                    if payload_request_id != request_id:
                        raise ValueError(
                            "bridge payload request_id does not match claimed filename"
                        )
                    result = handle(payload)
                    response = {
                        "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
                        "request_id": request_id,
                        "runtime_id": runtime_id,
                        "ok": True,
                        "result": result,
                        "observed_at": time.time(),
                    }
                except Exception as exc:
                    response = {
                        "schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1",
                        "request_id": request_id,
                        "runtime_id": runtime_id,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                        "observed_at": time.time(),
                    }
                _atomic_json(responses / f"{request_id}.json", response)
                claimed_path.unlink(missing_ok=True)
            time.sleep(0.1)
    finally:
        try:
            write_heartbeat()
        finally:
            cognition_ledger.close()
            portal_session.close()
            pre_active.close()
            if os.name == "nt" and mutex_handle:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(mutex_handle)
    return 0


def main() -> None:
    configured = os.environ.get("VERA_RUNTIME_ROOT")
    if not configured:
        raise SystemExit("VERA_RUNTIME_ROOT is required")
    raise SystemExit(run_host(Path(configured)))


if __name__ == "__main__":
    main()
