from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Protocol
import uuid

from .desktop_ipc import FileBridgeClient


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


class DesktopViewModel:
    def __init__(
        self,
        client: RuntimeClient,
        *,
        messaging_client: RuntimeClient | None = None,
    ) -> None:
        self.client = client
        self.messaging_client = messaging_client or client

    @staticmethod
    def _preferred_route(
        route_payloads: list[dict[str, object]],
    ) -> str | None:
        candidates = [
            route
            for route in route_payloads
            if route.get("available") is True
            and route.get("current") is True
            and route.get("auto_admissible") is True
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda route: (
                0 if route.get("local") is True else 1,
                0 if route.get("incremental_paid_compute") is False else 1,
                int(route.get("preference", 100)),
                str(route.get("route_id", "")),
            )
        )
        route_id = candidates[0].get("route_id")
        return str(route_id) if route_id is not None else None

    def refresh(self) -> DesktopSnapshot:
        status = self.client.request("desktop_status")
        route_payload = self.client.request("desktop_discover_routes")
        activity_payload = self.client.request(
            "desktop_recent_activity",
            limit=50,
        )
        routes_raw = route_payload.get("routes", [])
        activities_raw = activity_payload.get("items", [])
        routes = [
            dict(route)
            for route in routes_raw
            if isinstance(route, dict)
        ] if isinstance(routes_raw, list) else []
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
        return DesktopSnapshot(
            runtime_state=str(status.get("state", "UNKNOWN")),
            runtime_id=(
                str(status["runtime_id"])
                if status.get("runtime_id") is not None
                else None
            ),
            current_route=self._preferred_route(routes),
            last_autonomous_activity=last_autonomous,
            recent_activity=tuple(activities),
            pending_effects=(),
        )

    def fetch_conversation_history(
        self,
    ) -> tuple[dict[str, object], ...] | None:
        """None means this host does not implement history (not an empty chat)."""
        try:
            payload = self.client.request(
                "desktop_conversation_history", limit=50,
            )
        except RuntimeError as exc:
            if "unsupported command" in str(exc).lower():
                return None
            raise
        if payload.get("schema") != "PORTAL_DESKTOP_CONVERSATION_HISTORY_V1":
            raise ValueError("invalid conversation history response schema")
        raw = payload.get("items")
        if not isinstance(raw, list):
            raise ValueError("conversation history items must be a list")
        return tuple(dict(item) for item in raw if isinstance(item, dict))

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
        bound_request_id = request_id or uuid.uuid4().hex
        outcome = self.messaging_client.request(
            "desktop_cognize",
            request_id=bound_request_id,
            source="HUMAN",
            reason="desktop conversation message",
            task=task,
            created_at=float(time.time() if created_at is None else created_at),
            required_capabilities=["text"],
        )
        if outcome.get("request_id") not in (None, bound_request_id):
            raise ValueError("cognition result request_id mismatch")
        return outcome


def resolve_desktop_runtime_root(
    *,
    configured: str | None = None,
    installations_root: Path | None = None,
    process_alive: object | None = None,
    now: float | None = None,
) -> Path:
    """Choose a single live, installed Desktop host; never guess from recency.

    An explicit VERA_RUNTIME_ROOT remains an operator override, including for
    nonstandard layouts. Automatic selection uses installation identity,
    heartbeat freshness, component health and actual process liveness.
    """
    if configured is not None:
        if not configured.strip():
            raise ValueError("VERA_RUNTIME_ROOT must not be blank")
        return Path(configured).expanduser()

    from .desktop_supervisor import (
        RuntimeState,
        RuntimeSupervisor,
        RuntimeSupervisorConfig,
    )

    base = (
        Path(installations_root)
        if installations_root is not None
        else Path.home() / "AppData" / "Local" / "VeraDesktopRuntime"
    )
    active: list[Path] = []
    if base.is_dir():
        for candidate in sorted(base.iterdir()):
            if (
                not candidate.is_dir()
                or not (candidate / "RUNTIME_INSTALL_SPEC.json").is_file()
            ):
                continue
            options = (
                {"process_alive": process_alive}
                if process_alive is not None
                else {}
            )
            status = RuntimeSupervisor(
                RuntimeSupervisorConfig(candidate), **options,
            ).status(now=now)
            if status.state is RuntimeState.ACTIVE:
                active.append(candidate)

    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise RuntimeError(
            "multiple active PORTAL desktop runtimes; set "
            "VERA_RUNTIME_ROOT to the intended installation"
        )
    raise RuntimeError(
        "no active installed PORTAL desktop runtime found; start a "
        "qualified installed host or set VERA_RUNTIME_ROOT explicitly"
    )


class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("PORTAL Desktop — Vera")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        # Short liveness/telemetry timeouts keep a disconnected window usable.
        # Cognition can legitimately run much longer, on a separate client.
        self.client = FileBridgeClient(runtime_root, timeout_seconds=8.0)
        self.message_client = FileBridgeClient(
            runtime_root, timeout_seconds=180.0,
        )
        self.view_model = DesktopViewModel(
            self.client, messaging_client=self.message_client,
        )
        self._refresh_lock = threading.Lock()
        self._displayed_turns: dict[str, str] = {}

        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x")
        self.health_var = tk.StringVar(value="Runtime: checking…")
        self.route_var = tk.StringVar(value="Cognition route: unknown")
        ttk.Label(header, textvariable=self.health_var).pack(side="left")
        ttk.Label(header, textvariable=self.route_var).pack(side="right")

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
            value="Pending protected effects not reported by Desktop IPC."
        )
        ttk.Label(
            operations_frame,
            textvariable=self.effects_var,
            wraplength=360,
        ).pack(fill="x", pady=(2, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.status_var).pack(fill="x")

        self.root.after(100, self.refresh_async)
        self.root.after(3000, self._scheduled_refresh)

    @staticmethod
    def _activity_line(item: dict[str, object]) -> str:
        source = item.get("source", "UNKNOWN")
        state = item.get("state", "UNKNOWN")
        route = item.get("route_id") or "no-route"
        reason = item.get("reason") or ""
        return f"{source} · {state} · {route} · {reason}"

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

    def _restore_history(
        self, history: tuple[dict[str, object], ...] | list[dict[str, object]],
    ) -> None:
        """Replay only newly observed pieces of each exact request identity."""
        for row in history:
            request_id = row.get("request_id")
            task = row.get("task")
            if not isinstance(request_id, str) or not request_id:
                continue
            if not isinstance(task, str) or not task.strip():
                continue
            existing = self._displayed_turns.get(request_id)
            if existing is None:
                self._append_conversation("You", task)
                self._displayed_turns[request_id] = "USER"
            if self._displayed_turns[request_id] != "USER":
                continue
            state = row.get("state")
            answer = row.get("response_text")
            if state == "COMPLETED" and isinstance(answer, str) and answer.strip():
                self._append_conversation("Vera", answer)
                self._displayed_turns[request_id] = "FINAL"
            elif state == "FAILED":
                reason = row.get("error")
                self._append_conversation(
                    "System",
                    "Cognition failed: " + (
                        str(reason) if reason else "no diagnostic returned"
                    ),
                )
                self._displayed_turns[request_id] = "FINAL"

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
            f"Cognition route: {snapshot.current_route or 'none admissible'}"
        )
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
            "Pending protected effects not reported by Desktop IPC."
            if not snapshot.pending_effects
            else f"{len(snapshot.pending_effects)} pending exact effect request(s)."
        )

    def refresh_async(self) -> None:
        # The file bridge may take minutes to time out. A timer must never
        # multiply in-flight IPC refreshes while the host is unresponsive.
        if not self._refresh_lock.acquire(blocking=False):
            return

        def work() -> None:
            try:
                try:
                    snapshot = self.view_model.refresh()
                    history_loader = getattr(
                        self.view_model, "fetch_conversation_history", None
                    )
                    history = (
                        history_loader() if callable(history_loader) else None
                    )
                except Exception as exc:
                    # Python clears exception variables after the except
                    # block; capture rendered text before scheduling Tk work.
                    error = f"Runtime: DEGRADED · {type(exc).__name__}: {exc}"
                    self.root.after(
                        0,
                        lambda text=error: self.health_var.set(text),
                    )
                    return
                def update() -> None:
                    self._render_snapshot(snapshot)
                    if history is not None:
                        self._restore_history(history)

                self.root.after(0, update)
            finally:
                self._refresh_lock.release()

        try:
            threading.Thread(target=work, daemon=True).start()
        except BaseException:
            self._refresh_lock.release()
            raise

    def _scheduled_refresh(self) -> None:
        self.refresh_async()
        self.root.after(3000, self._scheduled_refresh)

    def _send_from_event(self, _event: object) -> str:
        self.send_message()
        return "break"

    def send_message(self) -> None:
        message = self.message_var.get().strip()
        if not message:
            return
        request_id = uuid.uuid4().hex
        self.message_var.set("")
        self._append_conversation("You", message)
        self._displayed_turns[request_id] = "USER"
        self.status_var.set("Awaiting runtime response...")
        self.send_button.configure(state="disabled")

        def work() -> None:
            from .desktop_ipc import BridgeOutcomeUnknownError

            try:
                outcome = self.view_model.send_message(
                    message, request_id=request_id,
                )
            except BridgeOutcomeUnknownError:
                # The host has taken custody. A duplicate send may repeat work.
                outcome = {
                    "state": "OUTCOME_UNKNOWN",
                    "error": "The host claimed this message but did not "
                    "return a response. Check the recovered history before "
                    "sending it again.",
                }
            except Exception as exc:
                outcome = {
                    "state": "CLIENT_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                }

            def finish() -> None:
                state = str(outcome.get("state", "UNKNOWN"))
                if state in {"COMPLETED", "FAILED"}:
                    self._restore_history((
                        {
                            **outcome,
                            "request_id": request_id,
                            "task": message,
                        },
                    ))
                elif state not in {"RUNNING", "UNRESOLVED"}:
                    self._append_conversation(
                        "System",
                        str(outcome.get("error") or
                            f"Runtime outcome was {state}; no verified reply."),
                    )
                route = outcome.get("route_id")
                self.status_var.set(
                    state + (f" via {route}" if route else "")
                )
                self.send_button.configure(state="normal")
                self.refresh_async()

            self.root.after(0, finish)

        threading.Thread(target=work, daemon=True).start()

    def run(self) -> None:
        self.message_entry.focus_set()
        self.root.mainloop()


def main() -> None:
    import os

    runtime_root = resolve_desktop_runtime_root(
        configured=os.environ.get("VERA_RUNTIME_ROOT"),
    )
    PortalDesktopApp(runtime_root=runtime_root).run()


if __name__ == "__main__":
    main()
