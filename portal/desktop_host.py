from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid

from .desktop_cognition import CognitionRequest, discover_cognition_routes, select_cognition_route
from .desktop_install import InstallSpec
from .desktop_ipc import DesktopRuntimeCommandHandler
from .desktop_pre_active import process_one_volition_signal
from .desktop_runtime import CognitionLedger, CognitionRequestEnvelope, ResidentCognitionEngine
from .desktop_vera_acceptance import VeraRuntimeAcceptance


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def _runtime_id(source_manifest: dict[str, object]) -> str:
    raw = json.dumps(source_manifest.get("sources", []), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prepare_runtime_layout(runtime_root: Path) -> dict[str, Path]:
    root = Path(runtime_root).resolve()
    state = root / "state"
    bridge = root / "bridge"
    layout = {"runtime_root": root, "state_root": state, "bridge_root": bridge,
              "requests": bridge / "requests", "claimed": bridge / "claimed", "responses": bridge / "responses"}
    for path in (state / "vera", state / "portal", state / "pre-active", state / "cognition",
                 layout["requests"], layout["claimed"], layout["responses"]):
        path.mkdir(parents=True, exist_ok=True)
    return layout


def claim_bridge_request(request_path: Path, claimed_dir: Path) -> Path | None:
    claimed_path = claimed_dir / request_path.name
    if claimed_path.exists():
        raise FileExistsError(f"bridge request already has a durable claim: {request_path.stem}")
    try:
        request_path.rename(claimed_path)
    except FileNotFoundError:
        return None
    return claimed_path


class RuntimeSingleton:
    """An OS-owned lock on the state root, independent of source revision/PID files."""

    def __init__(self, root: Path):
        self.path = Path(root).resolve() / "bridge" / "resident.lock"
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("resident runtime already owns this state directory") from exc
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
            self.stream = None


def load_manifest(root: Path) -> dict[str, object]:
    """Verify the install's exact clean source bindings before opening resident stores."""
    root = Path(root).resolve()
    spec_path = root / "RUNTIME_INSTALL_SPEC.json"
    spec = InstallSpec.read(spec_path)
    manifest = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    modules = {"vera-mono": "vera_core", "portal": "portal", "pre-active": "pre_active", "volition": "volition"}
    for component, source in spec.source_map().items():
        path = root / "sources" / component
        head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
        if head != source.sha or dirty:
            raise ValueError(f"{component}: source drift; explicit staged upgrade required")
        loaded = importlib.import_module(modules[component])
        if not loaded.__file__ or not Path(loaded.__file__).resolve().is_relative_to(path.resolve()):
            raise ValueError(f"{component}: imported package is outside bound source")
    policy = manifest.get("cognition_policy", {})
    if not isinstance(policy, dict) or type(policy.get("allow_local_no_paid_compute", False)) is not bool:
        raise ValueError("local cognition policy must be boolean")
    return manifest


class _PreActiveAdapter:
    def __init__(self, host: "ResidentHost"):
        self.host = host

    def respond(self, *, messages, tools):
        from pre_active.engine import ModelResponse, RetryableModelError
        # Public event listings omit claim owner and creation time. Read the
        # active owner's claim directly; old leases from a crashed owner must
        # not be mistaken for the current invocation.
        claimed = self.host.pre_active.connection.execute(
            "SELECT id,payload_json,created_at FROM events WHERE kind='run.step' AND status='CLAIMED' AND lease_owner=?",
            (self.host.worker_id,)).fetchall()
        if len(claimed) != 1:
            raise RuntimeError("cognition must bind exactly one claimed Pre-Active step")
        event = dict(claimed[0])
        event["payload"] = json.loads(event["payload_json"])
        run = self.host.pre_active.get_run(event["payload"]["run_id"])
        # get_run does not expose its source binding. Read through the owning
        # Store connection and Pre-Active's existing durable runs contract.
        run_binding = self.host.pre_active.connection.execute(
            "SELECT source_event_id FROM runs WHERE id=?", (run["id"],)).fetchone()
        source_event_id = run_binding["source_event_id"] if run_binding else None
        source_event = next((e for e in self.host.pre_active.list_events() if e["id"] == source_event_id), None)
        reason = source_event["payload"].get("reason", "Pre-Active resident run") if source_event else "Pre-Active resident run"
        request_id = "pre-active:" + event["id"]
        stored = self.host.ledger.get(request_id)
        task = str(stored["task"]) if stored else "\n\n".join(m["content"] for m in messages)
        request = CognitionRequestEnvelope(request_id, "PRE_ACTIVE_AUTONOMOUS_TURN", reason, task,
                                           float(event["created_at"]))
        result = self.host.cognition.process(request)
        self.host.pre_active.append_journal(
            event_type="PORTAL_COGNITION_OBSERVED", subject_id=run["id"],
            payload={"request_id": request_id, "source_event_id": source_event_id,
                     "run_step_event_id": event["id"], "state": result.state,
                     "evidence_id": result.evidence_id, "effect_authority": False}, now=time.time())
        if result.state != "COMPLETED":
            self.host.failure = result.error or result.state
            raise RetryableModelError(result.error or result.state, category="local_cognition", retry_after_seconds=30)
        return ModelResponse(final_text=result.response_text)


class ResidentHost:
    """One worker owns every mutable component API and its SQLite connections."""

    def __init__(self, root: Path, manifest: dict[str, object], *, discover=None, invoke=None):
        from vera_core import QualifiedVeraRuntime, VeraStateDirectory
        from portal import PortalCommandSession
        from pre_active import Store
        from pre_active.context import ContextAssembler
        from pre_active.daemon import Daemon
        from pre_active.engine import Engine
        from pre_active.observers import ObserverManager
        from pre_active.scheduler import Scheduler
        from pre_active.tools import ToolRegistry
        from pre_active.volition_bridge import VolitionBridge
        from volition import VolitionEngine

        self.root = Path(root).resolve()
        self.manifest = manifest
        self.runtime_id = _runtime_id(manifest)
        self.worker_id = self.runtime_id + ":" + uuid.uuid4().hex
        self.started_at = time.time()
        self.last_progress = self.started_at
        self.failure = None
        self.routes = ()
        self.selected_route = None
        state = prepare_runtime_layout(self.root)["state_root"]
        identity = manifest.get("identity", {"project_id": "VERA_MONO", "identity_id": "VERA_PROJECT_IDENTITY_V1"})
        self.vera_state = VeraStateDirectory(state / "vera", **identity)
        self.vera = QualifiedVeraRuntime.from_state_directory(self.vera_state)
        self.portal = PortalCommandSession(state / "portal" / "session.sqlite3")
        self.pre_active = Store(state / "pre-active" / "runtime.sqlite3")
        self.volition = VolitionBridge(self.pre_active)
        if self.pre_active.get_volition_state() is None:
            self.pre_active.save_volition_state(VolitionEngine().snapshot(), expected_revision=0, now=time.time())
        self.ledger = CognitionLedger(state / "cognition" / "desktop-cognition.sqlite3")
        self.discover = discover or (lambda: discover_cognition_routes(
            allow_local_no_paid_compute=manifest.get("cognition_policy", {}).get("allow_local_no_paid_compute", False)))
        self.discovery_interval_seconds = 0 if discover is not None else 5
        self.last_route_refresh = 0.
        options = {"invoke_route": invoke} if invoke is not None else {}
        self.cognition = ResidentCognitionEngine(
            ledger=self.ledger, discover_routes=self.refresh_routes,
            accept_result=VeraRuntimeAcceptance(self.vera, runtime_id=self.runtime_id), **options)
        engine = Engine(store=self.pre_active, model=_PreActiveAdapter(self),
                        tools=ToolRegistry(self.pre_active), context=ContextAssembler(self.pre_active),
                        system_prompt="You are Vera. Produce bounded cognition. No external effects are authorized.",
                        worker_id=self.worker_id, lease_seconds=300,
                        max_consecutive_endogenous_turns=0)
        self.daemon = Daemon(scheduler=Scheduler(self.pre_active), engine=engine, observers=ObserverManager(self.pre_active))
        self.handler = DesktopRuntimeCommandHandler(engine=self.cognition, discover_routes=self.refresh_routes, runtime_status=self.status)
        self.refresh_routes()

    def refresh_routes(self):
        self.routes = self.discover()
        self.selected_route = select_cognition_route(CognitionRequest(), self.routes)
        self.last_route_refresh = time.time()
        return self.routes

    def tick(self):
        try:
            if time.time() - self.last_route_refresh >= self.discovery_interval_seconds:
                self.refresh_routes()
            if self.selected_route is None:
                # Keep observer/scheduler/Volition resident; do not consume
                # cognition attempts while no route is admitted.
                now = time.time()
                observed = self.daemon.observers.tick(now=now)
                self.daemon.scheduler.tick(now=now)
                receipt = process_one_volition_signal(self.pre_active, now=now, worker_id=self.worker_id,
                                           bridge_factory=lambda store: self.volition)
                if receipt is not None:
                    self.failure = None
                if observed.errors:
                    self.failure = f"observer_errors:{observed.errors}"
            else:
                cycle = self.daemon.cycle(now=time.time())
                if cycle.run_id is not None:
                    self.failure = None
                if cycle.observer_errors:
                    self.failure = f"observer_errors:{cycle.observer_errors}"
        except Exception as exc:
            self.failure = f"{type(exc).__name__}: {exc}"
        self.last_progress = time.time()

    def activity(self, *, limit=200):
        payload = self.handler.handle({"command": "desktop_recent_activity", "limit": limit})
        bindings = {}
        rows = self.pre_active.connection.execute(
            "SELECT payload_json FROM journal WHERE event_type='PORTAL_COGNITION_OBSERVED' ORDER BY seq").fetchall()
        for row in rows:
            entry = json.loads(row["payload_json"])
            bindings[entry["request_id"]] = entry
        for item in payload["items"]:
            if item["request_id"] in bindings:
                item["source_event_id"] = bindings[item["request_id"]]["source_event_id"]
        return payload

    def status(self):
        failure = self.failure
        component_errors = {}
        try:
            vera_context = self.vera.resume_context()
        except Exception as exc:
            vera_context = {}
            component_errors["vera_mono"] = f"{type(exc).__name__}: {exc}"
            failure = component_errors["vera_mono"]
        try:
            pre_snapshot = self.pre_active.operational_snapshot(now=time.time())
        except Exception as exc:
            pre_snapshot = {}
            component_errors["pre_active"] = f"{type(exc).__name__}: {exc}"
            failure = component_errors["pre_active"]
        state = "DEGRADED" if failure else ("ACTIVE" if self.selected_route else "BLOCKED")
        volition_state = self.pre_active.get_volition_state()
        names = {"vera-mono": "vera_mono", "portal": "portal", "pre-active": "pre_active", "volition": "volition"}
        sources = {names[source["component"]]: source for source in self.manifest.get("sources", [])}
        classes = {"vera_mono": self.vera, "portal": self.portal, "pre_active": self.pre_active, "volition": self.volition}
        components = {name: {"loaded": True, "runtime_class": type(value).__name__,
                             "health": "DEGRADED" if name in component_errors else "ACTIVE",
                             "error": component_errors.get(name),
                             "sha": sources.get(name, {}).get("sha"),
                             "path": str(self.root / "sources" / name.replace("_", "-"))}
                      for name, value in classes.items()}
        components["vera_mono"]["restart_status"] = vera_context.get("restart_status")
        components["pre_active"].update({"operational_snapshot": pre_snapshot, "scheduler_resident": True, "observers_resident": True})
        components["volition"]["snapshot"] = volition_state
        activity = self.activity(limit=1)["items"]
        return {"schema": "VERA_DESKTOP_RUNTIME_STATUS_V1", "runtime_id": self.runtime_id,
                "pid": os.getpid(), "observed_at": time.time(), "started_at": self.started_at,
                "uptime_seconds": max(0., time.time() - self.started_at), "state": state,
                "failure": failure or (None if self.selected_route else "no_admissible_cognition_route"),
                "selected_route_id": self.selected_route.route_id if self.selected_route else None,
                "components": components, "source_manifest": self.manifest,
                "vera_currentness": vera_context, "last_cognition": activity[0] if activity else None,
                "last_progress_at": self.last_progress, "pending_effects": [],
                "protected_effect_authority": False, "native_openai_router_replaced": False}

    def handle(self, request):
        command = request.get("command")
        if command == "pre_active_snapshot":
            return self.pre_active.operational_snapshot(now=time.time())
        if command == "vera_context":
            return self.vera.resume_context()
        if command == "portal_status":
            return self.portal.status(str(request["session_id"]))
        if command == "desktop_portfolio":
            from .desktop_portfolio import DesktopPortfolioController
            if not hasattr(self, "portfolio"):
                self.portfolio = DesktopPortfolioController(self.root, session=self.portal)
            return self.portfolio.handle(request)
        if command == "desktop_recent_activity":
            return self.activity(limit=int(request.get("limit", 20)))
        result = self.handler.handle(request)
        if command == "desktop_cognize":
            self.failure = result.get("error") if result.get("state") == "FAILED" else None
            row = self.ledger.get(str(request["request_id"]))
            if row is not None:
                result["provenance"] = {
                    key: row[key] for key in ("source", "reason", "provider", "model_or_agent", "route_id",
                                             "created_at", "started_at", "completed_at", "evidence_id")}
                result["provenance"]["acceptance"] = json.loads(row["acceptance_json"]) if row["acceptance_json"] else None
        return result

    def close(self):
        self.ledger.close()
        self.pre_active.close()
        self.portal.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def serve(root: Path, manifest: dict[str, object], *, stop: threading.Event | None = None,
          host_factory: Callable = ResidentHost, worker_timeout_seconds: float = 210):
    """Heartbeat and cached telemetry stay responsive during bounded model calls."""
    stop = stop or threading.Event()
    layout = prepare_runtime_layout(root)
    bridge = layout["bridge_root"]
    commands = queue.Queue(maxsize=32)
    updates = queue.Queue()
    responses = queue.Queue()
    worker_observation = {"operation": "starting", "started_at": time.time()}
    observation_lock = threading.Lock()
    snapshot = {"schema": "VERA_DESKTOP_RUNTIME_STATUS_V1", "runtime_id": _runtime_id(manifest),
                "pid": os.getpid(), "state": "STARTING", "failure": None, "components": {},
                "source_manifest": manifest, "protected_effect_authority": False}
    routes, activity = [], {"schema": "PORTAL_DESKTOP_ACTIVITY_V1", "items": [], "protected_effect_authority": False}

    def begin_operation(operation):
        with observation_lock:
            worker_observation.update(operation=operation, started_at=time.time())

    def response_for(request_id, result=None, error=None):
        response = {"schema": "VERA_UNIFIED_BRIDGE_RESPONSE_V1", "request_id": request_id,
                    "runtime_id": _runtime_id(manifest), "ok": error is None, "observed_at": time.time()}
        response["result" if error is None else "error"] = result if error is None else error
        return response

    def worker():
        try:
            with host_factory(Path(root), manifest) as host:
                updates.put((host.status(), [asdict(route) for route in host.routes], host.activity()))
                while not stop.is_set():
                    try:
                        request = commands.get_nowait()
                    except queue.Empty:
                        request = None
                    if request is not None:
                        begin_operation(str(request.get("command")))
                        try:
                            result = host.handle(request)
                            responses.put(response_for(request["request_id"], result=result))
                        except Exception as exc:
                            host.failure = f"{type(exc).__name__}: {exc}"
                            responses.put(response_for(request["request_id"], error=host.failure))
                        host.last_progress = time.time()
                        updates.put((host.status(), [asdict(route) for route in host.routes], host.activity()))
                    begin_operation("resident_tick")
                    host.tick()
                    updates.put((host.status(), [asdict(route) for route in host.routes], host.activity()))
                    begin_operation("idle")
                    stop.wait(.1)
        except Exception as exc:
            updates.put(({**snapshot, "state": "BLOCKED", "failure": f"{type(exc).__name__}: {exc}"}, [], activity))

    thread = threading.Thread(target=worker, name="vera-state-owner", daemon=True)
    thread.start()
    queued = set()
    (bridge / "pid.txt").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    try:
        while not stop.is_set():
            try:
                while True:
                    snapshot, routes, activity = updates.get_nowait()
            except queue.Empty:
                pass
            try:
                while True:
                    response = responses.get_nowait()
                    _atomic_json(layout["responses"] / (response["request_id"] + ".json"), response)
                    queued.discard(response["request_id"] + ".json")
            except queue.Empty:
                pass
            snapshot = dict(snapshot)
            snapshot["observed_at"] = time.time()
            snapshot["worker_alive"] = thread.is_alive()
            with observation_lock:
                snapshot["worker_operation"] = worker_observation["operation"]
                snapshot["worker_operation_started_at"] = worker_observation["started_at"]
            if not thread.is_alive():
                snapshot["state"] = "BLOCKED"
                snapshot["failure"] = snapshot.get("failure") or "resident_worker_stopped"
            elif time.time() - snapshot["worker_operation_started_at"] > worker_timeout_seconds:
                snapshot["state"] = "DEGRADED"
                snapshot["failure"] = "resident_worker_not_progressing"
            _atomic_json(bridge / "heartbeat.json", snapshot)
            for path in sorted(layout["requests"].glob("*.json")):
                if (layout["responses"] / path.name).exists():
                    path.unlink(missing_ok=True)
                    continue
                try:
                    claim_bridge_request(path, layout["claimed"])
                except FileExistsError:
                    path.unlink(missing_ok=True)
            for path in sorted(layout["claimed"].glob("*.json")):
                if (layout["responses"] / path.name).exists():
                    path.unlink(missing_ok=True)
                    continue
                if path.name in queued:
                    continue
                try:
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", path.stem):
                        raise ValueError("invalid bridge request filename")
                    request = json.loads(path.read_text(encoding="utf-8-sig"))
                    if not isinstance(request, dict) or request.get("request_id") != path.stem:
                        raise ValueError("request ID does not match IPC filename")
                    command = request.get("command")
                    if command in {"desktop_status", "status", "ping"}:
                        result = snapshot
                    elif command == "desktop_discover_routes":
                        result = {"schema": "PORTAL_DESKTOP_COGNITION_ROUTES_V1", "routes": routes, "protected_effect_authority": False}
                    elif command == "desktop_recent_activity":
                        limit = int(request.get("limit", 20))
                        if not 1 <= limit <= 200:
                            raise ValueError("limit must be between 1 and 200")
                        result = {**activity, "items": activity["items"][:limit]}
                    elif command == "shutdown":
                        stop.set()
                        result = {"stopping": True, "runtime_id": snapshot["runtime_id"], "protected_effect_authority": False}
                    elif command in {"desktop_cognize", "desktop_portfolio", "pre_active_snapshot", "vera_context", "portal_status"}:
                        if not thread.is_alive():
                            raise RuntimeError("resident worker stopped; inspect heartbeat failure")
                        commands.put_nowait(request)
                        queued.add(path.name)
                        continue
                    else:
                        raise ValueError(f"unsupported resident command: {command!r}")
                    response = response_for(path.stem, result=result)
                except queue.Full:
                    continue
                except Exception as exc:
                    response = response_for(path.stem, error=f"{type(exc).__name__}: {exc}")
                _atomic_json(layout["responses"] / path.name, response)
                path.unlink(missing_ok=True)
            stop.wait(.1)
    finally:
        stop.set()
        # Keep the root singleton until the state owner closes all connections.
        # A permanently stalled worker needs native OS process termination.
        thread.join()
        _atomic_json(bridge / "heartbeat.json", {**snapshot, "state": "OFFLINE", "worker_alive": False,
                                                "observed_at": time.time()})


def run_host(runtime_root: Path, *, check: bool = False) -> int:
    runtime_root = Path(runtime_root).resolve()
    try:
        manifest = load_manifest(runtime_root)
        with RuntimeSingleton(runtime_root):
            if check:
                with ResidentHost(runtime_root, manifest) as host:
                    status = host.status()
                    healthy = status["state"] in {"ACTIVE", "BLOCKED"}
                    print(json.dumps({**status, "components_loaded": healthy}, sort_keys=True))
                    return 0 if healthy else 1
            serve(runtime_root, manifest)
        return 0
    except Exception as exc:
        runtime_root.mkdir(parents=True, exist_ok=True)
        with (runtime_root / "host-error.log").open("a", encoding="utf-8") as log:
            log.write(f"{time.time()}: {type(exc).__name__}: {exc}\n")
        print(f"Runtime startup failed: {exc}", file=sys.stderr)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Resident Vera desktop host")
    parser.add_argument("--runtime-root", type=Path, default=os.environ.get("VERA_RUNTIME_ROOT"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.runtime_root is None:
        parser.error("--runtime-root or VERA_RUNTIME_ROOT is required")
    return run_host(args.runtime_root, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
