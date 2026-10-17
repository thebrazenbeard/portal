from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Protocol
import uuid

from .desktop_ipc import BridgeOutcomeUnknownError, FileBridgeClient, canonical_locator


class DesktopConversationSelector:
    """Client UI pointer only. Task text, provenance and history stay in Mono."""

    def __init__(self, runtime_root: Path) -> None:
        self.path = Path(runtime_root) / "bridge" / "ui" / "active-conversation.json"

    def read(self) -> str | None:
        if not self.path.exists():
            if self.path.is_symlink():
                raise ValueError("CONVERSATION_SELECTOR_CORRUPT: BLOCKED")
            return None
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 4096:
                raise ValueError("invalid pointer file")

            def unique_fields(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate pointer field")
                    result[key] = value
                return result

            value = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
            if (type(value) is not dict or set(value) != {"schema", "conversation_id"}
                    or value["schema"] != "PORTAL_DESKTOP_UI_SELECTOR_V1"):
                raise ValueError("invalid pointer schema")
            return canonical_locator(value["conversation_id"], "conversation_locator")
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("CONVERSATION_SELECTOR_CORRUPT: BLOCKED; existing bytes preserved") from exc

    def begin_new(self, *, expected: str | None) -> str:
        # Reuse Mono's existing short-lived file-lock primitive. Its SQLite
        # lock contains no application state, task, identity or authority.
        from r8a0.portable_lock import PortableFileLock

        with PortableFileLock(self.path.with_suffix(".lock.sqlite3")):
            current = self.read()
            if current != expected:
                raise ValueError("CONVERSATION_SELECTOR_CHANGED: BLOCKED; reload selection")
            locator = "desktop-" + uuid.uuid4().hex
            temp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temp.open("x", encoding="utf-8") as stream:
                    json.dump({"schema": "PORTAL_DESKTOP_UI_SELECTOR_V1", "conversation_id": locator}, stream)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temp.replace(self.path)
            finally:
                temp.unlink(missing_ok=True)
            return locator


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
    def __init__(self, client: RuntimeClient, *, conversation_id: str | None = None,
                 selector: DesktopConversationSelector | None = None) -> None:
        self.client = client
        self.selector = selector
        self.conversation_id = selector.read() if selector is not None else conversation_id
        if self.conversation_id is not None:
            canonical_locator(self.conversation_id, "conversation_locator")
        if selector is not None and conversation_id is not None and conversation_id != self.conversation_id:
            raise ValueError("CONVERSATION_SELECTOR_CHANGED: BLOCKED")
        self._pending: tuple[str, str, str] | None = None
        if self.conversation_id is not None and isinstance(client, FileBridgeClient):
            self._pending = client.unresolved_cognition(self.conversation_id)

    def begin_new_conversation(self) -> str:
        if self._pending is not None:
            raise BridgeOutcomeUnknownError("reconcile original request before changing conversation")
        if self.selector is None:
            raise ValueError("client-owned conversation selector is required")
        self.conversation_id = self.selector.begin_new(expected=self.conversation_id)
        return self.conversation_id

    def conversation_history(self) -> dict[str, object]:
        return self.client.request("desktop_conversation_history",
                                   conversation_id=canonical_locator(self.conversation_id, "conversation_locator"))

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

    def send_message(
        self,
        message: str,
        *,
        request_id: str | None = None,
        created_at: float | None = None,
    ) -> dict[str, object]:
        if type(message) is not str or not message or message != message.strip() or len(message) > 2048:
            raise ValueError("invalid literal message: preserve text; remove surrounding whitespace explicitly")
        conversation_id = canonical_locator(self.conversation_id, "conversation_locator")
        if self.selector is not None and self.selector.read() != conversation_id:
            raise ValueError("CONVERSATION_SELECTOR_CHANGED: BLOCKED; reload selection")
        if self._pending is not None:
            old_conversation, old_id, old_text = self._pending
            if (conversation_id != old_conversation or message != old_text
                    or request_id not in (None, old_id)):
                raise BridgeOutcomeUnknownError(f"reconcile original request; no replacement: {old_id}")
            request_id = old_id
        request_id = canonical_locator(uuid.uuid4().hex if request_id is None else request_id, "request_id")
        self._pending = (conversation_id, request_id, message)
        try:
            result = self.client.request(
                "desktop_cognize", conversation_id=conversation_id, request_id=request_id,
                source="HUMAN", reason="desktop conversation message", task=message,
                created_at=float(time.time() if created_at is None else created_at),
                required_capabilities=["text"],
            )
        except BridgeOutcomeUnknownError:
            raise
        except Exception:
            self._pending = None
            raise
        if result.get("status") != "OUTCOME_UNKNOWN_NO_REDISPATCH":
            self._pending = None
        return result


class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("PORTAL Desktop — Vera")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        self.client = FileBridgeClient(runtime_root, timeout_seconds=180.0)
        selector = DesktopConversationSelector(runtime_root)
        selector_error = None
        try:
            self.view_model = DesktopViewModel(self.client, selector=selector)
        except (ValueError, BridgeOutcomeUnknownError) as exc:
            # Corrupt selectors remain blocked; read-only runtime UI stays open.
            selector_error = str(exc)
            self.view_model = DesktopViewModel(self.client)
            self.view_model.selector = selector

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
        ttk.Button(conversation_frame, text="Begin new canonical conversation",
                   command=self.begin_new_conversation).pack(anchor="w", pady=(4, 0))

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

        ttk.Label(operations_frame, text="Legacy operational activity (not canonical tasks)").pack(anchor="w")
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

        self.status_var = tk.StringVar(value=selector_error or (
            "Selected conversation is untrusted input; no model provider admitted."
            if self.view_model.conversation_id else "Begin a new canonical conversation. Legacy activity is not imported."
        ))
        ttk.Label(shell, textvariable=self.status_var).pack(fill="x")

        self.root.after(100, self.refresh_async)
        self.root.after(3000, self._scheduled_refresh)
        if self.view_model.conversation_id:
            self.root.after(100, self.load_history_async)

    def begin_new_conversation(self) -> None:
        try:
            self.view_model.begin_new_conversation()
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self._set_text(self.conversation, "New canonical conversation. No legacy history imported.\n\n")
        self.status_var.set("New conversation selected. No model provider admitted.")

    def load_history_async(self) -> None:
        selected = self.view_model.conversation_id

        def work() -> None:
            try:
                history = self.view_model.conversation_history()
                rendered = "\n\n".join(
                    f"Unverified user input: {row['text']}\n{row['status']}"
                    for row in history["items"]
                ) or "No canonical task history."
            except Exception as exc:
                rendered = f"Canonical history blocked: {exc}"
            def finish() -> None:
                if self.view_model.conversation_id == selected:
                    self._set_text(self.conversation, rendered)
            self.root.after(0, finish)
        threading.Thread(target=work, daemon=True).start()

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
            "None. Cognition carries no protected-effect authority."
            if not snapshot.pending_effects
            else f"{len(snapshot.pending_effects)} pending exact effect request(s)."
        )

    def refresh_async(self) -> None:
        def work() -> None:
            try:
                snapshot = self.view_model.refresh()
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: self.health_var.set(
                        f"Runtime: DEGRADED · {type(exc).__name__}: {exc}"
                    ),
                )
                return
            self.root.after(0, lambda: self._render_snapshot(snapshot))

        threading.Thread(target=work, daemon=True).start()

    def _scheduled_refresh(self) -> None:
        self.refresh_async()
        self.root.after(3000, self._scheduled_refresh)

    def _send_from_event(self, _event: object) -> str:
        self.send_message()
        return "break"

    def send_message(self) -> None:
        message = self.message_var.get()
        if not message:
            return
        self.message_var.set("")
        self._append_conversation("Patrick", message)
        self.status_var.set("Opening canonical task…")
        self.send_button.configure(state="disabled")

        def work() -> None:
            try:
                result = self.view_model.send_message(message)
                response = str(
                    result.get("response_text")
                    or result.get("error")
                    or result.get("state")
                    or "No response text."
                )
                route = result.get("route_id")
                state = result.get("state", "UNKNOWN")
            except Exception as exc:
                response = f"{type(exc).__name__}: {exc}"
                route = None
                state = "FAILED"

            def finish() -> None:
                self._append_conversation("Vera", response)
                self.status_var.set(
                    f"{state}"
                    + (f" via {route}" if route else "")
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

    configured = os.environ.get("VERA_RUNTIME_ROOT")
    runtime_root = Path(
        configured
        or Path.home()
        / "AppData"
        / "Local"
        / "VeraUnifiedRuntime"
        / "20261006"
    )
    PortalDesktopApp(runtime_root=runtime_root).run()


if __name__ == "__main__":
    main()
