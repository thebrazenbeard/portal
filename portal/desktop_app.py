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
        self, client: RuntimeClient, *, status_client: RuntimeClient | None = None,
    ) -> None:
        self.client = client
        self.status_client = status_client or client
        self.conversation_session_id = uuid.uuid4().hex

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
        status = self.status_client.request("desktop_status")
        route_payload = self.status_client.request("desktop_discover_routes")
        activity_payload = self.status_client.request(
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
        remember_context: bool = False,
        conversation_session_id: str | None = None,
    ) -> dict[str, object]:
        task = message.strip()
        if not task:
            raise ValueError("message is required")
        payload: dict[str, object] = {
            "source": "HUMAN",
            "reason": "desktop conversation message",
            "task": task,
            "created_at": float(time.time() if created_at is None else created_at),
            "required_capabilities": ["text"],
        }
        if remember_context:
            payload["conversation_session_id"] = (
                conversation_session_id or self.conversation_session_id
            )
        return self.client.request("desktop_cognize", request_id=request_id or uuid.uuid4().hex, **payload)


PORTAL_PALETTE = {
    # Lappy's structured dark desktop layout, with the supplied Vera artwork's
    # burgundy/rose color language. Branding never changes authority.
    "background": "#140910",
    "surface": "#210F1B",
    "surface_raised": "#321927",
    "editor": "#170A14",
    "border": "#623748",
    "text": "#FAE8E9",
    "muted": "#D6B5BC",
    "subtle": "#A17B88",
    "accent": "#EDA1A5",
    "accent_soft": "#F5B6B4",
    "accent_text": "#230C18",
    "warning": "#F7C790",
}

class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk

        c = PORTAL_PALETTE
        self._tk = tk
        self.root = tk.Tk()
        self.root.title("PORTAL Desktop — Vera")
        self.root.geometry("1280x820")
        self.root.minsize(1024, 680)
        self.root.configure(bg=c["background"])

        # Keep the qualified IPC and independent status/cognition timeouts.
        self.client = FileBridgeClient(runtime_root, timeout_seconds=180.0)
        self.status_client = FileBridgeClient(runtime_root, timeout_seconds=5.0)
        self.view_model = DesktopViewModel(
            self.client, status_client=self.status_client,
        )
        self._refresh_lock = threading.Lock()

        top = tk.Frame(self.root, bg=c["surface"], height=88)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        brand = tk.Frame(top, bg=c["surface"])
        brand.pack(side="left", padx=28, pady=14)
        icon_path = Path(__file__).resolve().parent / "assets" / "vera_icon.png"
        self._brand_image = None
        if icon_path.is_file():
            try:
                self._brand_image = tk.PhotoImage(file=str(icon_path)).subsample(2, 2)
            except tk.TclError:
                self._brand_image = None
        if self._brand_image is not None:
            tk.Label(brand, image=self._brand_image, bg=c["surface"],
                     borderwidth=0).pack(side="left", padx=(0, 15))
        else:
            mark = tk.Canvas(brand, width=52, height=52,
                             bg=c["surface"], highlightthickness=0)
            mark.pack(side="left", padx=(0, 15))
            mark.create_text(26, 26, text="V", fill=c["accent"],
                             font=("Segoe UI", 34, "bold"))
        titles = tk.Frame(brand, bg=c["surface"])
        titles.pack(side="left")
        tk.Label(titles, text="VERA", font=("Segoe UI", 21, "bold"),
                 bg=c["surface"], fg=c["text"]).pack(anchor="w")
        tk.Label(titles, text="PORTAL DESKTOP  /  LOCAL RUNTIME",
                 font=("Segoe UI", 8, "bold"), bg=c["surface"],
                 fg=c["muted"]).pack(anchor="w", pady=(0, 3))
        connection = tk.Frame(top, bg=c["surface"])
        connection.pack(side="right", padx=30, pady=15)
        self.health_var = tk.StringVar(value="Runtime: checking…")
        self.route_var = tk.StringVar(value="Cognition route: unknown")
        tk.Label(connection, textvariable=self.health_var,
                 font=("Segoe UI", 11, "bold"),
                 bg=c["surface"], fg=c["accent"]).pack(anchor="e")
        tk.Label(connection, text="LIVE RUNTIME TELEMETRY",
                 font=("Segoe UI", 8, "bold"),
                 bg=c["surface"], fg=c["subtle"]).pack(anchor="e", pady=(5, 0))

        shell = tk.Frame(self.root, bg=c["background"])
        shell.pack(fill="both", expand=True, padx=24, pady=(20, 8))
        body = tk.PanedWindow(
            shell, orient="horizontal", bg=c["background"], borderwidth=0,
            sashwidth=12, sashrelief="flat", relief="flat", showhandle=False,
        )
        body.pack(fill="both", expand=True)
        conversation_frame = tk.Frame(
            body, bg=c["surface"], highlightthickness=1,
            highlightbackground=c["border"],
        )
        operations = tk.Frame(body, bg=c["background"])
        body.add(conversation_frame, minsize=470, stretch="always")
        body.add(operations, minsize=310, stretch="always")

        intro = tk.Frame(conversation_frame, bg=c["surface"])
        intro.pack(fill="x", padx=22, pady=(22, 17))
        tk.Label(intro, text="01  /  CONVERSATION",
                 bg=c["surface"], fg=c["accent"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(intro, text="Talk to Vera", bg=c["surface"],
                 fg=c["text"], font=("Segoe UI", 22, "bold")
                 ).pack(anchor="w", pady=(7, 0))
        tk.Label(intro, text="Your conversation uses the selected local model route.",
                 bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 0))
        self.context_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(
            intro, text="Remember recent turns in this window (volatile; off by default)",
            variable=self.context_enabled, command=self._context_toggle,
            bg=c["surface"], fg=c["muted"],
            selectcolor=c["surface_raised"], activebackground=c["surface"],
            activeforeground=c["text"], font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(8, 0))

        transcript = tk.Frame(conversation_frame, bg=c["editor"],
                              highlightthickness=1,
                              highlightbackground=c["border"])
        transcript.pack(fill="both", expand=True, padx=18, pady=(0, 15))
        self.conversation = tk.Text(
            transcript, wrap="word", state="disabled", height=4,
            borderwidth=0, bg=c["editor"], fg=c["text"],
            insertbackground=c["accent"], selectbackground=c["border"],
            selectforeground=c["text"], relief="flat",
            font=("Segoe UI", 11), padx=19, pady=19, spacing3=9,
        )
        self.conversation.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(
            transcript, orient="vertical", command=self.conversation.yview,
            bg=c["surface_raised"], troughcolor=c["editor"], width=10,
            borderwidth=0, relief="flat",
        )
        scrollbar.pack(side="right", fill="y")
        self.conversation.configure(yscrollcommand=scrollbar.set)
        self.conversation.tag_configure(
            "speaker_you", foreground=c["accent"],
            font=("Segoe UI", 10, "bold"), spacing1=10,
        )
        self.conversation.tag_configure(
            "speaker_model", foreground=c["muted"],
            font=("Segoe UI", 10, "bold"), spacing1=10,
        )
        self.conversation.tag_configure(
            "message", foreground=c["text"],
            font=("Segoe UI", 11), spacing3=12,
        )
        self._empty_conversation = tk.Label(
            transcript,
            text="Your conversation begins here.",
            bg=c["editor"], fg=c["subtle"], font=("Segoe UI", 11),
        )
        self._empty_conversation.place(relx=0.5, rely=0.46, anchor="center")

        composer = tk.Frame(conversation_frame, bg=c["surface"])
        composer.pack(fill="x", padx=18, pady=(0, 19))
        entry_frame = tk.Frame(
            composer, bg=c["surface_raised"], highlightthickness=1,
            highlightbackground=c["border"],
        )
        entry_frame.pack(fill="x")
        self.message_var = tk.StringVar()
        self.message_entry = tk.Entry(
            entry_frame, textvariable=self.message_var,
            bg=c["surface_raised"], fg=c["text"], relief="flat",
            insertbackground=c["accent"], borderwidth=0,
            font=("Segoe UI", 11),
        )
        self.message_entry.pack(side="left", fill="x", expand=True,
                                padx=(16, 10), pady=13)
        self.message_entry.bind("<Return>", self._send_from_event)
        self.send_button = tk.Button(
            entry_frame, text="Send →", command=self.send_message,
            bg=c["accent"], fg=c["accent_text"],
            activebackground=c["accent_soft"], activeforeground=c["accent_text"],
            font=("Segoe UI", 10, "bold"), borderwidth=0,
            relief="flat", padx=22, pady=12, cursor="hand2",
        )
        self.send_button.pack(side="right", padx=5, pady=5)

        def panel(kicker: str, title: str, *, grow: bool = False) -> tk.Frame:
            frame = tk.Frame(operations, bg=c["surface"],
                             highlightthickness=1,
                             highlightbackground=c["border"])
            frame.pack(fill="both", expand=grow, pady=(0, 10))
            tk.Label(frame, text=kicker, bg=c["surface"],
                     fg=c["accent"], font=("Segoe UI", 8, "bold")
                     ).pack(anchor="w", padx=18, pady=(14, 5))
            tk.Label(frame, text=title, bg=c["surface"],
                     fg=c["text"], font=("Segoe UI", 13, "bold")
                     ).pack(anchor="w", padx=18, pady=(0, 5))
            return frame

        route_panel = panel("02  /  CONNECTIVITY", "Cognition route")
        tk.Label(route_panel, textvariable=self.route_var,
                 bg=c["surface"], fg=c["muted"], justify="left",
                 wraplength=405, font=("Segoe UI", 9)
                 ).pack(anchor="w", padx=18, pady=(0, 14))

        autonomous_panel = panel("03  /  RESIDENT", "Autonomous activity")
        self.autonomous_text = tk.Text(
            autonomous_panel, height=3, wrap="word", state="disabled",
            bg=c["surface"], fg=c["muted"], relief="flat", borderwidth=0,
            font=("Segoe UI", 9), padx=18, pady=4,
        )
        self.autonomous_text.pack(fill="x", pady=(0, 10))

        activity_panel = panel("04  /  TELEMETRY", "Recent activity", grow=True)
        self.activity_text = tk.Text(
            activity_panel, height=8, wrap="word", state="disabled",
            bg=c["surface"], fg=c["muted"], relief="flat", borderwidth=0,
            font=("Consolas", 9), padx=18, pady=7,
        )
        self.activity_text.pack(fill="both", expand=True, pady=(0, 10))

        effects_panel = panel("05  /  GOVERNANCE", "Protected effects")
        self.effects_var = tk.StringVar(
            value="None. Cognition carries no protected-effect authority."
        )
        tk.Label(effects_panel, textvariable=self.effects_var,
                 bg=c["surface"], fg=c["warning"], justify="left",
                 wraplength=405, font=("Segoe UI", 9)
                 ).pack(anchor="w", padx=18, pady=(0, 14))

        footer = tk.Frame(self.root, bg=c["background"])
        footer.pack(fill="x", padx=28, pady=(0, 13))
        self.status_var = tk.StringVar(value="")
        tk.Label(footer, textvariable=self.status_var,
                 bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(footer, text="LOCAL RUNTIME  ·  OBSERVATION ONLY",
                 bg=c["background"], fg=c["subtle"],
                 font=("Segoe UI", 8, "bold")).pack(side="right")

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
        if getattr(self, "_empty_conversation", None) is not None:
            self._empty_conversation.place_forget()
        tag = "speaker_you" if speaker in ("You", "Patrick") else "speaker_model"
        self.conversation.configure(state="normal")
        self.conversation.insert("end", speaker + "\n", tag)
        self.conversation.insert("end", text + "\n\n", "message")
        self.conversation.see("end")
        self.conversation.configure(state="disabled")

    @staticmethod
    def _response_speaker(route_id: object, state: object) -> str:
        if state != "COMPLETED" or not isinstance(route_id, str) or not route_id.strip():
            return "Runtime response"
        safe_route = "".join(char if char.isprintable() else " " for char in route_id.strip())
        return f"Cognition route ({safe_route[:96]})"

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
        if not self._refresh_lock.acquire(blocking=False):
            return

        def work() -> None:
            try:
                snapshot = self.view_model.refresh()
            except Exception as exc:
                error = f"Runtime: DEGRADED · {type(exc).__name__}: {exc}"
                self.root.after(0, lambda: self.health_var.set(error))
            else:
                self.root.after(0, lambda: self._render_snapshot(snapshot))
            finally:
                self._refresh_lock.release()

        threading.Thread(target=work, daemon=True).start()

    def _scheduled_refresh(self) -> None:
        self.refresh_async()
        self.root.after(3000, self._scheduled_refresh)

    def _send_from_event(self, _event: object) -> str:
        self.send_message()
        return "break"

    def _context_toggle(self) -> None:
        if not self.context_enabled.get():
            # Retire the handle so toggling back on cannot reattach the old
            # resident history. The old cache is ephemeral and LRU-bounded.
            self.view_model.conversation_session_id = uuid.uuid4().hex

    def send_message(self) -> None:
        message = self.message_var.get().strip()
        if not message:
            return
        self.message_var.set("")
        self._append_conversation("Patrick", message)
        self.status_var.set("Vera is thinking…")
        self.send_button.configure(state="disabled")
        # Tk widgets and session selection belong to the UI thread. Freeze
        # both before the worker starts; a mid-flight toggle must not silently
        # change this request's context binding.
        context_id = (
            self.view_model.conversation_session_id
            if self.context_enabled.get() else None
        )

        def work() -> None:
            try:
                result = self.view_model.send_message(
                    message, remember_context=context_id is not None,
                    conversation_session_id=context_id,
                )
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
                self._append_conversation(
                    self._response_speaker(route, state), response,
                )
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
