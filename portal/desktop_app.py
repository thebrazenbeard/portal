from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Protocol
import uuid

from .desktop_ipc import FileBridgeClient
from .desktop_portfolio import (
    DesktopProject,
    discover_github_portfolio,
    inspect_repository_head,
    load_curated_portfolio,
    run_governed_portfolio_cycle,
)


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


PORTAL_PALETTE = {
    "background": "#0A1019",
    "surface": "#101A27",
    "surface_raised": "#162333",
    "editor": "#0D1724",
    "border": "#26364A",
    "text": "#ECF4FA",
    "muted": "#97A9BB",
    "subtle": "#69839B",
    "accent": "#62E6CF",
    "accent_text": "#061B1C",
    "success": "#62E6CF",
    "warning": "#FFC78A",
}


class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk

        c = PORTAL_PALETTE
        self._tk = tk
        self.root = tk.Tk()
        self.root.title("PORTAL Desktop — Vera")
        self.root.geometry("1280x820")
        self.root.minsize(1050, 760)
        self.root.configure(bg=c["background"])

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

        # Deliberately use only native Tk widgets: no dependency on a web
        # server, browser, external theme package, or generated image asset.
        top = tk.Frame(self.root, bg=c["surface"], height=82)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)

        brand = tk.Frame(top, bg=c["surface"])
        brand.pack(side="left", padx=(30, 12), pady=17)
        mark = tk.Canvas(
            brand, width=42, height=42, bg=c["surface"],
            highlightthickness=0,
        )
        mark.pack(side="left", padx=(0, 12))
        mark.create_oval(
            5, 5, 37, 37, outline=c["accent"], width=2,
        )
        mark.create_text(
            21, 21, text="P", fill=c["accent"],
            font=("Segoe UI", 19, "bold"),
        )
        titles = tk.Frame(brand, bg=c["surface"])
        titles.pack(side="left")
        tk.Label(
            titles, text="PORTAL", bg=c["surface"], fg=c["text"],
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles, text="PORTFOLIO OPERATIONS  /  VERA",
            bg=c["surface"], fg=c["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")

        connection = tk.Frame(top, bg=c["surface"])
        connection.pack(side="right", padx=30, pady=14)
        self.health_var = tk.StringVar(value="Runtime: checking…")
        self.route_var = tk.StringVar(value="Cognition route: checking…")
        tk.Label(
            connection, textvariable=self.health_var,
            bg=c["surface"], fg=c["accent"],
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="e")
        tk.Label(
            connection, text="LIVE RUNTIME TELEMETRY",
            bg=c["surface"], fg=c["subtle"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="e", pady=(5, 0))

        # The operational surface comes second: messaging is the primary work.
        shell = tk.Frame(self.root, bg=c["background"])
        shell.pack(fill="both", expand=True, padx=24, pady=(22, 14))
        body = tk.PanedWindow(
            shell, orient="horizontal",
            bg=c["background"], borderwidth=0,
            sashwidth=14, sashrelief="flat", relief="flat",
            showhandle=False,
        )
        body.pack(fill="both", expand=True)

        conversation_frame = tk.Frame(
            body, bg=c["surface"],
            highlightthickness=1, highlightbackground=c["border"],
        )
        operations_frame = tk.Frame(body, bg=c["background"])
        body.add(conversation_frame, minsize=480, stretch="always")
        body.add(operations_frame, minsize=310, stretch="always")

        conversation_title = tk.Frame(conversation_frame, bg=c["surface"])
        conversation_title.pack(fill="x", padx=24, pady=(22, 18))
        tk.Label(
            conversation_title, text="01  /  CONVERSATION",
            bg=c["surface"], fg=c["accent"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            conversation_title, text="Talk to Vera",
            bg=c["surface"], fg=c["text"],
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            conversation_title,
            text="Messages use the selected local cognition route.",
            bg=c["surface"], fg=c["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        transcript_frame = tk.Frame(
            conversation_frame, bg=c["editor"],
            highlightthickness=1, highlightbackground=c["border"],
        )
        transcript_frame.pack(
            fill="both", expand=True, padx=20, pady=(0, 16),
        )
        self.conversation = tk.Text(
            transcript_frame,
            wrap="word", state="disabled", height=4, borderwidth=0,
            bg=c["editor"], fg=c["text"],
            insertbackground=c["accent"],
            selectbackground="#304A58", selectforeground=c["text"],
            font=("Segoe UI", 11), relief="flat",
            padx=22, pady=20, spacing1=4, spacing3=10,
            cursor="arrow",
        )
        self.conversation.pack(
            side="left", fill="both", expand=True,
        )
        scroll = tk.Scrollbar(
            transcript_frame, orient="vertical",
            command=self.conversation.yview,
            bg=c["surface_raised"], troughcolor=c["editor"],
            activebackground=c["border"], width=10,
            borderwidth=0, relief="flat",
        )
        scroll.pack(side="right", fill="y")
        self.conversation.configure(yscrollcommand=scroll.set)
        self.conversation.tag_configure(
            "speaker_you", foreground=c["accent"],
            font=("Segoe UI", 10, "bold"),
            spacing1=12,
        )
        self.conversation.tag_configure(
            "speaker_vera", foreground="#B9B3FF",
            font=("Segoe UI", 10, "bold"),
            spacing1=12,
        )
        self.conversation.tag_configure(
            "speaker_system", foreground=c["warning"],
            font=("Segoe UI", 10, "bold"),
            spacing1=12,
        )
        self.conversation.tag_configure(
            "message", foreground=c["text"],
            font=("Segoe UI", 11), spacing3=14,
        )
        self._empty_conversation = tk.Label(
            transcript_frame,
            text="Your conversation begins here.\n\n"
                 "Messages and responses are recovered from the runtime.",
            justify="center", bg=c["editor"], fg=c["muted"],
            font=("Segoe UI", 11),
        )
        self._empty_conversation.place(
            relx=0.5, rely=0.44, anchor="center",
        )

        composer = tk.Frame(conversation_frame, bg=c["surface"])
        composer.pack(fill="x", padx=20, pady=(0, 20))
        compose_border = tk.Frame(
            composer, bg=c["surface_raised"],
            highlightthickness=1, highlightbackground=c["border"],
        )
        compose_border.pack(fill="x")
        self.message_var = tk.StringVar()
        self.message_entry = tk.Entry(
            compose_border, textvariable=self.message_var,
            bg=c["surface_raised"], fg=c["text"],
            insertbackground=c["accent"], borderwidth=0,
            relief="flat", font=("Segoe UI", 11),
            disabledbackground=c["surface_raised"],
        )
        self.message_entry.pack(
            side="left", fill="x", expand=True,
            padx=16, pady=15,
        )
        self.message_entry.bind("<Return>", self._send_from_event)
        self.send_button = tk.Button(
            compose_border, text="Send  →",
            command=self.send_message,
            bg=c["accent"], fg=c["accent_text"],
            activebackground="#A3F4E8",
            activeforeground=c["accent_text"],
            font=("Segoe UI", 10, "bold"),
            borderwidth=0, relief="flat", cursor="hand2",
            padx=20, pady=12,
        )
        self.send_button.pack(side="right", padx=5, pady=5)

        # The right column is read-only observed state, not simulated controls.
        def panel(kicker: str, heading: str, *,
                  expanding: bool = False) -> tk.Frame:
            outer = tk.Frame(
                operations_frame, bg=c["surface"],
                highlightthickness=1, highlightbackground=c["border"],
            )
            outer.pack(
                fill="both" if expanding else "x",
                expand=expanding,
                pady=(0, 10),
            )
            inner = tk.Frame(outer, bg=c["surface"])
            inner.pack(fill="both", expand=True, padx=18, pady=12)
            tk.Label(
                inner, text=kicker, bg=c["surface"], fg=c["accent"],
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            tk.Label(
                inner, text=heading, bg=c["surface"], fg=c["text"],
                font=("Segoe UI", 14, "bold"),
            ).pack(anchor="w", pady=(3, 7))
            return inner

        route_panel = panel("02  /  CONNECTIVITY", "Cognition route")
        tk.Label(
            route_panel, textvariable=self.route_var,
            wraplength=295, justify="left",
            bg=c["surface"], fg=c["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        autonomous_panel = panel("03  /  RESIDENT", "Autonomous activity")
        self.autonomous_text = tk.Text(
            autonomous_panel, height=2, state="disabled",
            bg=c["surface"], fg=c["muted"],
            font=("Segoe UI", 10), wrap="word",
            borderwidth=0, relief="flat",
            highlightthickness=0, cursor="arrow",
        )
        self.autonomous_text.pack(fill="x")

        activity_panel = panel("04  /  TELEMETRY", "Recent activity", expanding=True)
        self.activity_text = tk.Text(
            activity_panel, height=4, state="disabled",
            bg=c["surface"], fg=c["muted"],
            font=("Segoe UI", 10), wrap="word",
            borderwidth=0, relief="flat",
            highlightthickness=0, cursor="arrow",
            spacing3=9,
        )
        self.activity_text.pack(fill="both", expand=True)

        effects_panel = panel("05  /  GOVERNANCE", "Protected effects")
        self.effects_var = tk.StringVar(
            value="Pending protected effects not reported by Desktop IPC."
        )
        self.effects_label = tk.Label(
            effects_panel, textvariable=self.effects_var,
            wraplength=295, justify="left",
            bg=c["surface"], fg=c["warning"],
            font=("Segoe UI", 10),
        )
        self.effects_label.pack(anchor="w")

        # Portfolio owns the primary workspace. The Vera messaging surface is
        # retained as a separate tab; no dummy "run project" buttons.
        self._source_root = Path(__file__).resolve().parents[1]
        self._runtime_root = Path(runtime_root)
        self._vera_widgets = (conversation_title, transcript_frame, composer)
        self._portfolio_panel = tk.Frame(conversation_frame, bg=c["surface"])
        workspace_header = tk.Frame(conversation_frame, bg=c["surface"])
        workspace_header.pack(fill="x", before=conversation_title, padx=20, pady=(12, 0))
        self._portfolio_nav = tk.Button(
            workspace_header, text="Portfolio",
            command=lambda: self._show_workspace("portfolio"),
            bg=c["surface_raised"], fg=c["accent"],
            activebackground=c["border"], activeforeground=c["text"],
            borderwidth=0, relief="flat", font=("Segoe UI", 10, "bold"),
            padx=16, pady=10,
        )
        self._portfolio_nav.pack(side="left")
        self._vera_nav = tk.Button(
            workspace_header, text="Vera",
            command=lambda: self._show_workspace("vera"),
            bg=c["surface"], fg=c["muted"],
            activebackground=c["border"], activeforeground=c["text"],
            borderwidth=0, relief="flat", font=("Segoe UI", 10, "bold"),
            padx=16, pady=10,
        )
        self._vera_nav.pack(side="left", padx=(7, 0))
        tk.Label(
            self._portfolio_panel, text="PROJECT PORTFOLIO",
            bg=c["surface"], fg=c["accent"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=22, pady=(18, 2))
        tk.Label(
            self._portfolio_panel, text="Operate the portfolio",
            bg=c["surface"], fg=c["text"],
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w", padx=22)
        self.portfolio_count_var = tk.StringVar(value="Reading curated registry…")
        tk.Label(
            self._portfolio_panel, textvariable=self.portfolio_count_var,
            bg=c["surface"], fg=c["muted"], font=("Segoe UI", 10),
            wraplength=500, justify="left",
        ).pack(anchor="w", padx=22, pady=(4, 14))
        list_frame = tk.Frame(
            self._portfolio_panel,
            bg=c["editor"], highlightthickness=1,
            highlightbackground=c["border"],
        )
        list_frame.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        self.project_listbox = tk.Listbox(
            list_frame,
            bg=c["editor"], fg=c["text"], selectbackground=c["border"],
            selectforeground=c["accent"], highlightthickness=1,
            highlightbackground=c["border"], relief="flat",
            activestyle="none", borderwidth=0,
            font=("Segoe UI", 11), height=9,
        )
        self.project_listbox.pack(
            side="left", fill="both", expand=True, padx=(7, 0), pady=6,
        )
        project_scrollbar = tk.Scrollbar(
            list_frame, orient="vertical",
            command=self.project_listbox.yview,
            bg=c["surface_raised"], troughcolor=c["editor"],
            activebackground=c["border"], width=10,
            borderwidth=0, relief="flat",
        )
        project_scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=6)
        self.project_listbox.configure(yscrollcommand=project_scrollbar.set)
        self.project_listbox.bind("<<ListboxSelect>>", self._project_selected)
        self.project_detail_var = tk.StringVar(value="Select a project to inspect its declared execution targets.")
        tk.Label(
            self._portfolio_panel, textvariable=self.project_detail_var,
            bg=c["surface"], fg=c["muted"],
            wraplength=520, justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=22, pady=(0, 12))
        actions = tk.Frame(self._portfolio_panel, bg=c["surface"])
        actions.pack(fill="x", padx=22)
        self.project_refresh_button = tk.Button(
            actions, text="Reload registry", command=self._reload_portfolio,
            bg=c["surface_raised"], fg=c["text"], borderwidth=0,
            relief="flat", padx=12, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.project_refresh_button.pack(side="left")
        self.project_live_button = tk.Button(
            actions, text="Sync GitHub",
            command=self._sync_github_portfolio,
            bg=c["surface_raised"], fg=c["text"], borderwidth=0,
            relief="flat", padx=12, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.project_live_button.pack(side="left", padx=(8, 0))
        self.project_inspect_button = tk.Button(
            actions, text="Inspect main",
            command=self._inspect_selected_project,
            bg=c["surface_raised"], fg=c["text"], borderwidth=0,
            relief="flat", padx=12, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.project_inspect_button.pack(side="left", padx=(8, 0))
        self.portfolio_run_button = tk.Button(
            actions, text="Run curated cycle",
            command=self._run_portfolio_cycle,
            bg=c["accent"], fg=c["accent_text"], borderwidth=0,
            relief="flat", padx=12, pady=10, font=("Segoe UI", 9, "bold"),
        )
        self.portfolio_run_button.pack(side="left", padx=(8, 0))
        self.portfolio_action_var = tk.StringVar(
            value="No execution started. Work requires a qualified route."
        )
        tk.Label(
            self._portfolio_panel, textvariable=self.portfolio_action_var,
            bg=c["surface"], fg=c["warning"], wraplength=520,
            justify="left", font=("Segoe UI", 9),
        ).pack(anchor="w", padx=22, pady=(12, 18))
        self._project_items: tuple[DesktopProject, ...] = ()
        self._portfolio_action_lock = threading.Lock()
        self._reload_portfolio()
        self._show_workspace("portfolio")

        footer = tk.Frame(self.root, bg=c["background"])
        footer.pack(side="bottom", fill="x", padx=28, pady=(0, 15))
        self.status_var = tk.StringVar(value="Ready.")
        self.footer_status = tk.Label(
            footer, textvariable=self.status_var,
            bg=c["background"], fg=c["muted"],
            font=("Segoe UI", 9),
        )
        self.footer_status.pack(side="left")
        tk.Label(
            footer, text="LOCAL RUNTIME  ·  OBSERVATION ONLY",
            bg=c["background"], fg=c["subtle"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="right")

        self.root.after(100, self.refresh_async)
        self.root.after(3000, self._scheduled_refresh)

    def _show_workspace(self, workspace: str) -> None:
        if workspace not in {"portfolio", "vera"}:
            raise ValueError("unknown workspace")
        self._portfolio_panel.pack_forget()
        for widget in self._vera_widgets:
            widget.pack_forget()
        c = PORTAL_PALETTE
        if workspace == "portfolio":
            self._portfolio_panel.pack(
                fill="both", expand=True, pady=(0, 12),
            )
        else:
            self._vera_widgets[0].pack(fill="x", padx=24, pady=(14, 12))
            self._vera_widgets[1].pack(
                fill="both", expand=True, padx=20, pady=(0, 16),
            )
            self._vera_widgets[2].pack(
                fill="x", padx=20, pady=(0, 20),
            )
        self._portfolio_nav.configure(
            fg=c["accent"] if workspace == "portfolio" else c["muted"],
        )
        self._vera_nav.configure(
            fg=c["accent"] if workspace == "vera" else c["muted"],
        )

    def _reload_portfolio(self) -> None:
        try:
            projects = load_curated_portfolio(self._source_root)
        except Exception as exc:
            self.portfolio_action_var.set(
                f"Portfolio registry unavailable: {type(exc).__name__}: {exc}"
            )
            return
        self._project_items = projects
        self.project_listbox.delete(0, "end")
        for project in projects:
            self.project_listbox.insert(
                "end", f"  {project.name}  ·  {project.repository}"
            )
        self.portfolio_count_var.set(
            f"{len(projects)} curated repository entries · static registry, "
            "not a live GitHub inventory. New repositories may be missing."
        )
        if projects:
            self.project_listbox.selection_set(0)
            self._project_selected(None)

    def _sync_github_portfolio(self) -> None:
        if not self._portfolio_action_lock.acquire(blocking=False):
            self.portfolio_action_var.set("An action is already in progress.")
            return
        self.portfolio_action_var.set("Reading authenticated live GitHub inventory…")

        def worker() -> None:
            try:
                projects = discover_github_portfolio(self._source_root)
                error = None
            except Exception as exc:
                projects = ()
                error = f"GitHub sync unavailable: {type(exc).__name__}: {exc}"
            finally:
                self._portfolio_action_lock.release()

            def finish() -> None:
                if error is not None:
                    self.portfolio_action_var.set(error)
                    return
                self._project_items = projects
                self.project_listbox.delete(0, "end")
                for project in projects:
                    self.project_listbox.insert(
                        "end", f"  {project.name}  ·  {project.repository}"
                    )
                self.portfolio_count_var.set(
                    f"{len(projects)} authenticated live, nonarchived GitHub "
                    "repositories. Uncurated entries have no execution target."
                )
                self.portfolio_action_var.set(
                    "Live membership refreshed. This alone does not grant "
                    "execution or merge authority."
                )
                if projects:
                    self.project_listbox.selection_set(0)
                    self._project_selected(None)
            self.root.after(0, finish)
        try:
            threading.Thread(target=worker, daemon=True).start()
        except BaseException:
            self._portfolio_action_lock.release()
            raise

    def _project_selected(self, _event: object) -> None:
        selection = self.project_listbox.curselection()
        if not selection or not self._project_items:
            return
        project = self._project_items[int(selection[0])]
        targets = ", ".join(project.targets) if project.targets else "None configured"
        self.project_detail_var.set(
            f"{project.repository}\n"
            f"Visibility: {project.visibility} · Scheduling: {project.scheduling_state}\n"
            f"Declared execution targets: {targets}. "
            "No arbitrary project launch command is configured."
        )

    def _start_portfolio_action(self, title: str, operation: object) -> None:
        if not self._portfolio_action_lock.acquire(blocking=False):
            self.portfolio_action_var.set("An action is already in progress.")
            return
        self.portfolio_action_var.set(f"{title}…")

        def worker() -> None:
            try:
                result = operation()
                message = f"{title}: {result}"
            except Exception as exc:
                message = f"{title} unavailable: {type(exc).__name__}: {exc}"
            finally:
                self._portfolio_action_lock.release()
            self.root.after(
                0, lambda value=message: self.portfolio_action_var.set(value),
            )

        try:
            threading.Thread(target=worker, daemon=True).start()
        except BaseException:
            self._portfolio_action_lock.release()
            raise

    def _inspect_selected_project(self) -> None:
        selection = self.project_listbox.curselection()
        if not selection:
            self.portfolio_action_var.set("Select a repository first.")
            return
        repository = self._project_items[int(selection[0])].repository
        self._start_portfolio_action(
            "Exact main-head inspection",
            lambda: f"{repository}@{inspect_repository_head(repository)}",
        )

    def _run_portfolio_cycle(self) -> None:
        # Explicit operator click starts a real, one-cycle PORTAL command.
        # No driver or effect authority is supplied by this UI.
        self._start_portfolio_action(
            "Governed curated-registry cycle (qualified route required)",
            lambda: run_governed_portfolio_cycle(
                self._source_root, self._runtime_root,
            ).get("summary", "completed without a summary"),
        )

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
        empty = getattr(self, "_empty_conversation", None)
        if empty is not None:
            empty.place_forget()
            self._empty_conversation = None
        self.conversation.configure(state="normal")
        tag = "speaker_" + speaker.lower()
        self.conversation.insert("end", speaker.upper() + "\n", tag)
        self.conversation.insert("end", text + "\n\n", "message")
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
