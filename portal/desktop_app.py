from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
from typing import Protocol
import uuid

from .desktop_ipc import FileBridgeClient
from .desktop_supervisor import RuntimeState, RuntimeSupervisor, RuntimeSupervisorConfig


class RuntimeClient(Protocol):
    def request(self, command: str, **payload: object) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class DesktopSnapshot:
    runtime_state: str
    runtime_id: str | None
    current_route: str | None
    last_autonomous_activity: dict[str, object] | None
    recent_activity: tuple[dict[str, object], ...]
    pending_effects: tuple[dict[str, object], ...]
    health_reason: str = ""
    heartbeat_age_seconds: float | None = None
    components: dict[str, object] = field(default_factory=dict)


class DesktopViewModel:
    def __init__(
        self, client: RuntimeClient, *, supervisor: RuntimeSupervisor | None = None,
        status_client: RuntimeClient | None = None,
    ) -> None:
        self.client = client
        self.status_client = status_client or client
        self.supervisor = supervisor

    def refresh(self) -> DesktopSnapshot:
        health = None
        if self.supervisor is not None:
            try:
                health = self.supervisor.ensure_started()
            except (OSError, ValueError) as exc:
                return DesktopSnapshot(
                    "BLOCKED", None, None, None, (), (),
                    health_reason=f"{type(exc).__name__}: {exc}",
                )
            if health.state is not RuntimeState.ACTIVE:
                return DesktopSnapshot(
                    health.state.value, health.runtime_id, None, None, (), (),
                    health_reason=health.reason,
                    heartbeat_age_seconds=health.heartbeat_age_seconds,
                )
        status = self.status_client.request("desktop_status")
        activity_payload = self.status_client.request(
            "desktop_recent_activity",
            limit=50,
        )
        activities_raw = activity_payload.get("items", [])
        activities = [
            dict(item)
            for item in activities_raw
            if isinstance(item, dict)
        ] if isinstance(activities_raw, list) else []
        last_autonomous = next(
            (
                item
                for item in activities
                if item.get("source") == "PRE_ACTIVE_AUTONOMOUS_TURN"
            ),
            None,
        )
        state = str(status.get("state", "DEGRADED"))
        if state not in {item.value for item in RuntimeState}:
            state = "DEGRADED"
        pending = status.get("pending_effects", [])
        components = status.get("components", {})
        selected_route = status.get("selected_route_id")
        return DesktopSnapshot(
            runtime_state=state,
            runtime_id=(
                str(status["runtime_id"])
                if status.get("runtime_id") is not None
                else None
            ),
            current_route=str(selected_route) if selected_route else None,
            last_autonomous_activity=last_autonomous,
            recent_activity=tuple(activities),
            pending_effects=tuple(item for item in pending if isinstance(item, dict))
            if isinstance(pending, list) else (),
            health_reason=str(status.get("failure") or status.get("reason") or ""),
            heartbeat_age_seconds=health.heartbeat_age_seconds if health else None,
            components=dict(components) if isinstance(components, dict) else {},
        )

    def send_message(
        self,
        message: str,
        *,
        request_id: str | None = None,
        created_at: float | None = None,
    ) -> dict[str, object]:
        task = message.strip()
        if not task:
            raise ValueError("message is required")
        return self.client.request(
            "desktop_cognize",
            request_id=request_id or uuid.uuid4().hex,
            source="HUMAN",
            reason="desktop conversation message",
            task=task,
            created_at=float(time.time() if created_at is None else created_at),
            required_capabilities=["text"],
        )


class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("P.O.R.T.A.L. Desktop — Vera")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        self.client = FileBridgeClient(runtime_root, timeout_seconds=180.0)
        self.view_model = DesktopViewModel(
            self.client,
            supervisor=RuntimeSupervisor(RuntimeSupervisorConfig(runtime_root)),
            status_client=FileBridgeClient(runtime_root, timeout_seconds=5.0),
        )
        self._events: Queue[tuple[str, object]] = Queue()
        self._closed = False
        self._refresh_in_flight = False
        self._send_in_flight = False
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x")
        self.health_var = tk.StringVar(value="Runtime: checking…")
        self.route_var = tk.StringVar(value="Cognition route: unknown")
        ttk.Label(header, textvariable=self.health_var).pack(side="left")
        ttk.Label(header, textvariable=self.route_var).pack(side="right")
        self.details_var = tk.StringVar(value="Waiting for runtime health and source versions.")
        ttk.Label(shell, textvariable=self.details_var, wraplength=1050).pack(fill="x")

        body = ttk.Panedwindow(shell, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 8))

        conversation_frame = ttk.Labelframe(body, text="Conversation", padding=8)
        operations_frame = ttk.Labelframe(body, text="Runtime", padding=8)
        body.add(conversation_frame, weight=3)
        body.add(operations_frame, weight=2)

        self.conversation = tk.Text(
            conversation_frame,
            wrap="word",
            state="disabled",
            height=28,
        )
        self.conversation.pack(fill="both", expand=True)

        composer = ttk.Frame(conversation_frame)
        composer.pack(fill="x", pady=(8, 0))
        self.message_var = tk.StringVar()
        self.message_entry = ttk.Entry(
            composer,
            textvariable=self.message_var,
        )
        self.message_entry.pack(side="left", fill="x", expand=True)
        self.message_entry.bind("<Return>", self._send_from_event)
        self.send_button = ttk.Button(
            composer,
            text="Send",
            command=self.send_message,
        )
        self.send_button.pack(side="left", padx=(8, 0))

        ttk.Label(operations_frame, text="Last autonomous activity").pack(
            anchor="w"
        )
        self.autonomous_text = tk.Text(
            operations_frame,
            height=8,
            wrap="word",
            state="disabled",
        )
        self.autonomous_text.pack(fill="x", pady=(2, 8))

        ttk.Label(operations_frame, text="Activity").pack(anchor="w")
        self.activity_text = tk.Text(
            operations_frame,
            height=16,
            wrap="word",
            state="disabled",
        )
        self.activity_text.pack(fill="both", expand=True, pady=(2, 8))

        ttk.Label(operations_frame, text="Pending protected effects").pack(
            anchor="w"
        )
        self.effects_var = tk.StringVar(
            value="None. Cognition carries no protected-effect authority."
        )
        ttk.Label(
            operations_frame,
            textvariable=self.effects_var,
            wraplength=360,
        ).pack(fill="x", pady=(2, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.status_var).pack(fill="x")

        self.root.after(100, self.refresh_async)
        self.root.after(50, self._drain_events)
        self.root.after(3000, self._scheduled_refresh)

    @staticmethod
    def _activity_line(item: dict[str, object]) -> str:
        source = item.get("source", "UNKNOWN")
        state = item.get("state", "UNKNOWN")
        route = item.get("route_id") or "no-route"
        reason = item.get("reason") or ""
        evidence = item.get("evidence_id") or "no evidence"
        return f"{source} · {state} · {route} · {reason} · {evidence}"

    def _set_text(self, widget: object, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _append_conversation(self, speaker: str, text: str) -> None:
        self.conversation.configure(state="normal")
        self.conversation.insert("end", f"{speaker}: {text}\n\n")
        self.conversation.see("end")
        self.conversation.configure(state="disabled")

    def _render_snapshot(self, snapshot: DesktopSnapshot) -> None:
        runtime_id = (
            snapshot.runtime_id[:12] + "…"
            if snapshot.runtime_id and len(snapshot.runtime_id) > 12
            else snapshot.runtime_id or "unknown"
        )
        self.health_var.set(
            f"Runtime: {snapshot.runtime_state} · {runtime_id}"
        )
        self.route_var.set(
            f"Cognition route: {snapshot.current_route or 'none selected by runtime'}"
        )
        details = [snapshot.health_reason] if snapshot.health_reason else []
        if snapshot.heartbeat_age_seconds is not None:
            details.append(f"Heartbeat age: {snapshot.heartbeat_age_seconds:.1f}s")
        for name, component in snapshot.components.items():
            if isinstance(component, dict):
                revision = component.get("source_revision") or component.get("sha") or component.get("version") or "unverified version"
                details.append(f"{name}: {revision} ({'loaded' if component.get('loaded') else 'unavailable'})")
        self.details_var.set(" · ".join(details))
        if snapshot.last_autonomous_activity is None:
            autonomous = "No autonomous cognition recorded."
        else:
            autonomous = self._activity_line(
                snapshot.last_autonomous_activity
            )
        self._set_text(self.autonomous_text, autonomous)
        activity = "\n".join(
            self._activity_line(item)
            for item in snapshot.recent_activity[:30]
        ) or "No cognition activity recorded."
        self._set_text(self.activity_text, activity)
        self.effects_var.set(
            "None. Cognition carries no protected-effect authority."
            if not snapshot.pending_effects
            else "\n".join(json.dumps(effect, sort_keys=True) for effect in snapshot.pending_effects)
        )

    def refresh_async(self) -> None:
        if self._closed or self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        def work() -> None:
            try:
                snapshot = self.view_model.refresh()
            except Exception as exc:
                self._events.put(("refresh_error", f"{type(exc).__name__}: {exc}"))
                return
            self._events.put(("snapshot", snapshot))

        threading.Thread(target=work, daemon=True).start()

    def _scheduled_refresh(self) -> None:
        if self._closed:
            return
        self.refresh_async()
        self.root.after(3000, self._scheduled_refresh)

    def _drain_events(self) -> None:
        # Every Tk operation, including after(), runs on the GUI thread.
        if self._closed:
            return
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except Empty:
                break
            if kind == "snapshot":
                self._refresh_in_flight = False
                self._render_snapshot(payload)
            elif kind == "refresh_error":
                self._refresh_in_flight = False
                self.health_var.set(f"Runtime: DEGRADED · {payload}")
            elif kind == "response":
                self._send_in_flight = False
                self._render_response(payload)
        self.root.after(50, self._drain_events)

    def _render_response(self, result: dict[str, object]) -> None:
        response = str(result.get("response_text") or result.get("error") or result.get("state") or "No response text.")
        self._append_conversation("Vera", response)
        provenance = " · ".join(
            f"{key}: {result[key]}"
            for key in ("state", "request_id", "route_id", "provider", "model_or_agent", "evidence_id", "retryable")
            if result.get(key) is not None
        )
        self._append_conversation("Provenance", provenance)
        self.status_var.set(str(result.get("state", "UNKNOWN")))
        self.send_button.configure(state="normal")
        self.refresh_async()

    def _send_from_event(self, _event: object) -> str:
        self.send_message()
        return "break"

    def send_message(self) -> None:
        if self._closed or self._send_in_flight:
            return
        message = self.message_var.get().strip()
        if not message:
            return
        self.message_var.set("")
        self._append_conversation("Patrick", message)
        self.status_var.set("Vera is thinking…")
        self.send_button.configure(state="disabled")
        self._send_in_flight = True

        def work() -> None:
            try:
                result = self.view_model.send_message(message)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}", "state": "FAILED"}
            self._events.put(("response", result))

        threading.Thread(target=work, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        self.root.destroy()

    def run(self) -> None:
        self.message_entry.focus_set()
        self.root.mainloop()


def default_runtime_root() -> Path:
    configured = os.environ.get("VERA_RUNTIME_ROOT")
    if configured:
        return Path(configured)
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local_app_data / "PortalVera" / "runtime"


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="P.O.R.T.A.L. Desktop — resident Vera interface")
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    args = parser.parse_args(argv)
    PortalDesktopApp(runtime_root=args.runtime_root).run()


if __name__ == "__main__":
    main()
