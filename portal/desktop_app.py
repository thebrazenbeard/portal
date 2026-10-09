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
from .desktop_action_guard import load_operator_pins, prepare_portfolio_request
from .desktop_repo_jobs import RepoJobQueue
from .discovery import GitHubRepositoryCatalog
from .desktop_portfolio import DesktopPortfolioController, _existing_github_token
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
            if health.state is not RuntimeState.ACTIVE and health.components_loaded is not True:
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
        if health is not None and health.state is not RuntimeState.ACTIVE:
            state = health.state.value
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
            health_reason=health.reason if health and health.state is not RuntimeState.ACTIVE else str(status.get("failure") or status.get("reason") or ""),
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

    def portfolio(self, action: str = "status", *, session_id: str = "portfolio", **payload) -> dict[str, object]:
        return self.client.request("desktop_portfolio", action=action, session_id=session_id, **payload)

    def owned_inventory(self, *, session_id: str = "portfolio") -> dict[str, object]:
        """Show authenticated GitHub membership without changing resident state.

        This is a deliberately read-only UI query, not an executable wave,
        worker admission, or a claim that the resident has newer features.
        """
        # The currently installed resident lacks the inventory command;
        # invoking unsupported IPC would poison its persistent health status.
        # This path only reads metadata via the existing GitHub connection.
        status = self.portfolio("status", session_id=session_id)
        profile = status.get("profile")
        if not isinstance(profile, dict) or not profile.get("wave"):
            raise ValueError("no installed portfolio profile to establish GitHub owner")
        owner = (profile.get("discover_owner")
                 or DesktopPortfolioController._infer_owner(Path(profile["wave"])))
        token = _existing_github_token()
        if not token:
            raise ValueError("authenticated GitHub login is required for complete portfolio inventory")
        items = GitHubRepositoryCatalog(token=token).list_owned_repositories(str(owner))
        return {
            "portfolio_source": "LIVE_GITHUB_READ_ONLY_FRONTEND",
            "protected_effect_authority": False,
            "repositories": [{
                "name": item.name, "full_name": item.full_name,
                "visibility": "PRIVATE" if item.private else "PUBLIC",
                "archived": item.archived, "default_branch": item.default_branch
            } for item in items]
        }

    def approve_authority(
        self,
        request_id: str,
        *,
        valid_for_seconds: float = 300.0,
    ) -> dict[str, object]:
        return self.client.request(
            "desktop_authority",
            action="approve",
            request_id=request_id,
            valid_for_seconds=valid_for_seconds,
        )

    def deny_authority(self, request_id: str) -> dict[str, object]:
        return self.client.request(
            "desktop_authority",
            action="deny",
            request_id=request_id,
        )


class PortalDesktopApp:
    @staticmethod
    def _configure_theme(ttk: object) -> None:
        """Compact dark control-room theme with visible action hierarchy."""
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        bg, panel, field = "#0a1221", "#142036", "#1e304b"
        fg, subdued, accent = "#eaf1fc", "#9caec8", "#60d4e9"
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=panel, foreground=fg,
                        font=("Segoe UI", 10))
        style.configure("Hero.TLabel", background=bg, foreground=fg,
                        font=("Segoe UI Semibold", 19))
        style.configure("Quiet.TLabel", background=bg, foreground=subdued,
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=bg, foreground=accent,
                        font=("Segoe UI Semibold", 10))
        style.configure("TLabelframe", background=panel, bordercolor=field)
        style.configure("TLabelframe.Label", background=panel, foreground=accent,
                        font=("Segoe UI Semibold", 10))
        style.configure("TButton", background=field, foreground=fg,
                        padding=(11, 7), font=("Segoe UI Semibold", 10))
        style.map("TButton", background=[("active", "#2a4165"), ("disabled", panel)],
                  foreground=[("disabled", subdued)])
        style.configure("Primary.TButton", background=accent, foreground="#07121f",
                        padding=(14, 7))
        style.map("Primary.TButton", background=[("active", "#8de6f4")])
        style.configure("TEntry", fieldbackground=field, foreground=fg,
                        bordercolor=field, padding=(7, 6))
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=subdued,
                        padding=(14, 9))
        style.map("TNotebook.Tab", background=[("selected", field)],
                  foreground=[("selected", fg)])
        style.configure("Treeview", background=panel, fieldbackground=panel,
                        foreground=fg, rowheight=31, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=field, foreground=fg,
                        relief="flat", font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#285a78")],
                  foreground=[("selected", "#ffffff")])

    def __init__(self, *, runtime_root: Path, worker_pins_file: Path | None = None, preview: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("P.O.R.T.A.L. SOURCE PREVIEW — Vera" if preview else "P.O.R.T.A.L. Desktop — Vera")
        self.root.geometry("1280x840")
        self.root.minsize(1024, 700)
        self._configure_theme(ttk)
        self.root.configure(background="#0a1221")
        self._worker_pins_file = worker_pins_file
        self._operator_pins: dict[str, str] | None = None
        self._last_portfolio: dict[str, object] | None = None
        if worker_pins_file is not None:
            self._operator_pins = load_operator_pins(worker_pins_file)

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
        header.pack(fill="x", pady=(1, 7))
        ttk.Label(header, text="P.O.R.T.A.L.", style="Hero.TLabel").pack(side="left")
        self.health_var = tk.StringVar(value="Checking resident runtime")
        self.route_var = tk.StringVar(value="Cognition route: unknown")
        ttk.Label(header, textvariable=self.health_var, style="Status.TLabel").pack(side="right")
        ttk.Label(shell, text="VERA  /  LOCAL OPERATIONS  /  SOURCE-ONLY PROPOSALS",
                  style="Quiet.TLabel").pack(anchor="w")
        ttk.Label(shell, textvariable=self.route_var, style="Quiet.TLabel").pack(anchor="w")
        self.details_var = tk.StringVar(value="Waiting for resident health and queue state.")
        ttk.Label(shell, textvariable=self.details_var, wraplength=1180,
                  style="Quiet.TLabel").pack(fill="x", pady=(6, 0))

        body = ttk.Panedwindow(shell, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 8))

        workspace = ttk.Notebook(body)
        inventory_frame = ttk.Frame(workspace, padding=8)
        portfolio_frame = ttk.Frame(workspace, padding=8)
        conversation_frame = ttk.Frame(workspace, padding=8)
        workspace.add(inventory_frame, text="All repositories")
        workspace.add(portfolio_frame, text="Work queue")
        workspace.add(conversation_frame, text="Conversation")
        self.inventory_var = tk.StringVar(
            value="Connecting to authenticated GitHub inventory…")
        inventory_header = ttk.Frame(inventory_frame)
        inventory_header.pack(fill="x", pady=(0, 8))
        ttk.Label(inventory_header, textvariable=self.inventory_var,
                  style="Quiet.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(inventory_header, text="Sync GitHub",
                   command=self.refresh_inventory).pack(side="right")
        ttk.Button(inventory_header, text="Queue selected",
                   command=self.queue_selected_repository,
                   style="Primary.TButton").pack(side="right", padx=(0, 8))
        ttk.Label(inventory_frame, style="Quiet.TLabel",
                  text="Inventory is visibility, not permission to run workers. "
                       "Held and delegated work stays protected."
                  ).pack(fill="x", pady=(0, 8))
        self.repository_tree = ttk.Treeview(
            inventory_frame, columns=("repository", "visibility", "state", "branch"),
            show="headings")
        for key, label, width in (
            ("repository", "Repository", 255),
            ("visibility", "Visibility", 90),
            ("state", "Repository status", 115),
            ("branch", "Default branch", 110)):
            self.repository_tree.heading(key, text=label)
            self.repository_tree.column(key, width=width,
                                        stretch=(key=="repository"))
        scroll = ttk.Scrollbar(inventory_frame, orient="vertical",
                               command=self.repository_tree.yview)
        self.repository_tree.configure(yscrollcommand=scroll.set)
        self.repository_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._inventory_in_flight = False
        self._repo_job_in_flight = False
        self._repo_jobs = RepoJobQueue(
            Path(os.environ.get("PORTAL_LOCAL_PROPOSAL_ROOT")
                 or (Path.home() / "AppData" / "Local" / "P.O.R.T.A.L"
                     / "isolated-source-proposals")))
        self._repo_job_rows = {}
        self._inventory_notebook = workspace
        self._queue_tab = portfolio_frame
        self._conversation_tab = conversation_frame
        operations_frame = ttk.Labelframe(body, text="Runtime", padding=8)
        body.add(workspace, weight=4)
        body.add(operations_frame, weight=1)
        self.root.after(300, lambda: body.sashpos(
            0, int(body.winfo_width() * 0.70)))
        runtime_tabs = ttk.Notebook(operations_frame)
        runtime_tabs.pack(fill="both", expand=True)
        runtime_summary = ttk.Frame(runtime_tabs, padding=7)
        runtime_logs = ttk.Frame(runtime_tabs, padding=7)
        runtime_tabs.add(runtime_summary, text="Status")
        runtime_tabs.add(runtime_logs, text="Diagnostics")

        controls = ttk.Frame(portfolio_frame)
        controls.pack(fill="x")
        self.session_var = tk.StringVar(value="portfolio")
        ttk.Label(controls, text="SESSION", style="Quiet.TLabel").pack(side="left")
        ttk.Entry(controls, textvariable=self.session_var, width=17).pack(side="left", padx=8)
        ttk.Button(controls, text="Profile…", command=self.choose_profile).pack(side="left")
        ttk.Button(controls, text="Worker pins…",
                   command=self.choose_worker_pins).pack(side="left", padx=(8, 0))
        self.worker_pins_var = tk.StringVar(
            value="Pin manifest selected (consistency only)" if self._operator_pins
            else "Worker locked: choose pinned manifest before dispatch")
        ttk.Label(portfolio_frame, textvariable=self.worker_pins_var,
                  style="Quiet.TLabel").pack(fill="x", pady=(8, 1))
        self.portfolio_var = tk.StringVar(
            value=(
                "Run connects the live portfolio automatically. "
                "Advanced profile is an optional manual override."
            )
        )
        ttk.Label(portfolio_frame, textvariable=self.portfolio_var, wraplength=620).pack(fill="x", pady=8)
        actions = ttk.Frame(portfolio_frame)
        actions.pack(fill="x")
        for label, action in (("Refresh", "status"), ("Run resident", "run"),
                              ("Continue", "continue"), ("Hold selected", "hold"),
                              ("Stop", "stop")):
            ttk.Button(actions, text=label,
                       style="Primary.TButton" if action == "status" else "TButton",
                       command=lambda value=action: self.portfolio_action(value)
                       ).pack(side="left", padx=(0, 7))
        self.portfolio_tree = ttk.Treeview(portfolio_frame, columns=("subject", "state", "route", "verification"), show="headings")
        for key, label in (("subject", "Repository / work"), ("state", "State"), ("route", "Worker route"), ("verification", "Verified")):
            self.portfolio_tree.heading(key, text=label)
            self.portfolio_tree.column(key, width=220 if key == "subject" else 115)
        self.portfolio_tree.pack(fill="both", expand=True, pady=8)
        self._portfolio_subjects = {}
        self._portfolio_in_flight = False
        jobs_heading = ttk.Frame(portfolio_frame)
        jobs_heading.pack(fill="x", pady=(14, 5))
        ttk.Label(jobs_heading, text="SELECTED LOCAL PROPOSAL JOBS",
                  style="Status.TLabel").pack(side="left")
        self.local_job_run_button = ttk.Button(
            jobs_heading, text="Run next queued (local Ollama)",
            command=self.run_next_local_job, style="Primary.TButton")
        self.local_job_run_button.pack(side="right")
        ttk.Button(jobs_heading, text="View proposal",
                   command=self.view_selected_local_proposal
                   ).pack(side="right", padx=(0, 8))
        self.local_job_var = tk.StringVar(value="No local proposals queued.")
        ttk.Label(portfolio_frame, textvariable=self.local_job_var,
                  style="Quiet.TLabel").pack(fill="x")
        self.local_jobs_tree = ttk.Treeview(
            portfolio_frame, columns=("repository","head","state","result"),
            show="headings", height=6)
        for key, label, width in (
            ("repository","Repository",250), ("head","Source commit",105),
            ("state","Status",145), ("result","Proposal / blocker",235)):
            self.local_jobs_tree.heading(key,text=label)
            self.local_jobs_tree.column(key,width=width,
                                        stretch=key in {"repository","result"})
        self.local_jobs_tree.pack(fill="both", expand=True, pady=(5, 3))
        self._render_local_jobs()

        self.conversation = tk.Text(
            conversation_frame, wrap="word", state="disabled", height=28,
            background="#142036", foreground="#eaf1fc",
            insertbackground="#60d4e9", selectbackground="#295b75",
            relief="flat", font=("Segoe UI", 11), padx=13, pady=13)
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

        ttk.Label(runtime_summary, text="LAST RESIDENT ACTIVITY",
                  style="Status.TLabel").pack(anchor="w")
        self.autonomous_text = tk.Text(
            runtime_summary, height=6, wrap="word", state="disabled",
            background="#142036", foreground="#eaf1fc",
            insertbackground="#60d4e9", relief="flat", padx=9, pady=9,
            font=("Segoe UI", 10))
        self.autonomous_text.pack(fill="x", pady=(2, 8))

        ttk.Label(runtime_logs, text="EVENT RECEIPTS  ·  READ-ONLY",
                  style="Status.TLabel").pack(anchor="w")
        self.activity_text = tk.Text(
            runtime_logs, height=14, wrap="word", state="disabled",
            background="#142036", foreground="#eaf1fc",
            insertbackground="#60d4e9", relief="flat", padx=9, pady=9,
            font=("Segoe UI", 10))
        self.activity_text.pack(fill="both", expand=True, pady=(2, 8))

        ttk.Label(runtime_summary, text="PENDING AUTHORITY REQUESTS",
                  style="Status.TLabel").pack(anchor="w", pady=(10, 2))
        self.effects_var = tk.StringVar(
            value="None. Cognition carries no protected-effect authority."
        )
        ttk.Label(
            runtime_summary,
            textvariable=self.effects_var,
            wraplength=360,
        ).pack(fill="x", pady=(2, 2))
        self.effects_list = tk.Listbox(
            runtime_summary, height=6, exportselection=False,
            background="#142036", foreground="#eaf1fc",
            selectbackground="#285a78", relief="flat",
            font=("Segoe UI", 10))
        self.effects_list.pack(fill="x", pady=(2, 4))
        self._pending_effects: tuple[dict[str, object], ...] = ()

        effect_buttons = ttk.Frame(runtime_summary)
        effect_buttons.pack(fill="x")
        self._effect_buttons = effect_buttons
        self.approve_button = ttk.Button(
            effect_buttons,
            text="Approve",
            command=self.approve_selected_authority,
        )
        self.approve_button.pack(side="left")
        self.deny_button = ttk.Button(
            effect_buttons,
            text="Deny",
            command=self.deny_selected_authority,
        )
        self.deny_button.pack(side="left", padx=(8, 0))
        # A missing authority request must not look like an actionable grant.
        self.approve_button.configure(state="disabled")
        self.deny_button.configure(state="disabled")
        self.effects_list.pack_forget()
        self._effect_buttons.pack_forget()

        self.status_var = tk.StringVar(value="")
        ttk.Label(shell, textvariable=self.status_var).pack(fill="x")

        self.root.after(100, self.refresh_async)
        self.root.after(500, lambda: self.portfolio_action("status"))
        self.root.after(1000, self.refresh_inventory)
        self.root.after(50, self._drain_events)
        self.root.after(3000, self._scheduled_refresh)

    @staticmethod
    def _activity_line(item: dict[str, object]) -> str:
        source = item.get("source", "UNKNOWN")
        state = item.get("state", "UNKNOWN")
        route = item.get("route_id") or "no-route"
        reason = item.get("reason") or ""
        evidence = item.get("evidence_id") or "no evidence"
        summary = f"{source}  ·  {state}  ·  {route}"
        if reason:
            summary += f"\n{str(reason)[:120]}"
        if evidence != "no evidence":
            summary += f"  ·  receipt {str(evidence)[:16]}…"
        return summary

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
        # The primary screen is for decisions, not full SHA dumps.
        summary = []
        if snapshot.health_reason:
            summary.append("Last resident error: " + snapshot.health_reason[:125])
        if snapshot.heartbeat_age_seconds is not None:
            summary.append(f"heartbeat {snapshot.heartbeat_age_seconds:.0f}s")
        loaded = sum(
            isinstance(item, dict) and item.get("loaded") is True
            for item in snapshot.components.values())
        if snapshot.components:
            summary.append(f"{loaded}/{len(snapshot.components)} components loaded")
        self.details_var.set("  ·  ".join(summary) or "Resident status checked")
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
        self._pending_effects = snapshot.pending_effects
        button_state = "normal" if snapshot.pending_effects else "disabled"
        self.approve_button.configure(state=button_state)
        self.deny_button.configure(state=button_state)
        if snapshot.pending_effects:
            self.effects_list.pack(fill="x", pady=(2,4))
            self._effect_buttons.pack(fill="x")
        else:
            self.effects_list.pack_forget()
            self._effect_buttons.pack_forget()
        self.effects_list.delete(0, "end")
        for effect in snapshot.pending_effects:
            self.effects_list.insert(
                "end",
                (
                    f"{effect.get('effect_class', 'UNKNOWN')} · "
                    f"{effect.get('target', 'unknown target')} · "
                    f"{effect.get('summary', '')}"
                ),
            )
        self.effects_var.set(
            "None. Cognition carries no protected-effect authority."
            if not snapshot.pending_effects
            else (
                f"{len(snapshot.pending_effects)} exact authority request(s). "
                "Approve mints Project Runner grant(s); it does not itself "
                "perform the protected effect."
            )
        )

    def _selected_authority_request(self) -> dict[str, object]:
        selection = self.effects_list.curselection()
        if not selection:
            raise ValueError("select a pending authority request first")
        index = int(selection[0])
        if index < 0 or index >= len(self._pending_effects):
            raise ValueError("selected authority request is stale")
        return self._pending_effects[index]

    def approve_selected_authority(self) -> None:
        from tkinter import messagebox

        try:
            effect = self._selected_authority_request()
            request_id = str(effect.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("selected authority request has no request id")
        except Exception as exc:
            self.status_var.set(f"Authority: {type(exc).__name__}: {exc}")
            return

        if not messagebox.askyesno(
            "Approve exact authority",
            (
                f"{effect.get('effect_class', 'UNKNOWN')}\n"
                f"{effect.get('target', 'unknown target')}\n\n"
                f"{effect.get('summary', '')}\n\n"
                "Approve will mint fresh Project Runner authority for this "
                "exact request. It will not bypass review, currentness, "
                "fencing, or backend verification."
            ),
            parent=self.root,
        ):
            return

        self.status_var.set("Minting exact authority grant(s)…")
        self.approve_button.configure(state="disabled")
        self.deny_button.configure(state="disabled")

        def work() -> None:
            try:
                result = self.view_model.approve_authority(request_id)
                message = (
                    f"APPROVED {request_id}; "
                    f"execution_performed={result.get('execution_performed')}"
                )
            except Exception as exc:
                message = (
                    f"Authority approval failed: {type(exc).__name__}: {exc}"
                )
            self._events.put(("authority_result", message))

        threading.Thread(target=work, daemon=True).start()

    def deny_selected_authority(self) -> None:
        try:
            effect = self._selected_authority_request()
            request_id = str(effect.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("selected authority request has no request id")
        except Exception as exc:
            self.status_var.set(f"Authority: {type(exc).__name__}: {exc}")
            return

        self.approve_button.configure(state="disabled")
        self.deny_button.configure(state="disabled")

        def work() -> None:
            try:
                result = self.view_model.deny_authority(request_id)
                message = (
                    f"DENIED {request_id}; "
                    f"execution_performed={result.get('execution_performed')}"
                )
            except Exception as exc:
                message = (
                    f"Authority denial failed: {type(exc).__name__}: {exc}"
                )
            self._events.put(("authority_result", message))

        threading.Thread(target=work, daemon=True).start()

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
            elif kind == "portfolio":
                self._portfolio_in_flight = False
                self._render_portfolio(payload)
            elif kind == "inventory":
                self._inventory_in_flight = False
                self._render_inventory(payload)
            elif kind == "inventory_error":
                self._inventory_in_flight = False
                self.repository_tree.delete(*self.repository_tree.get_children())
                self.inventory_var.set("Inventory unavailable: " + str(payload))
            elif kind == "local_job":
                self._repo_job_in_flight = False
                outcome, data = payload
                self._render_local_jobs()
                if outcome == "queued":
                    self.inventory_var.set(
                        f"Queued {data['repository']} at {data['head'][:10]}. "
                        "Open Work queue to run the local proposal.")
                    self._inventory_notebook.select(self._queue_tab)
                    self.portfolio_var.set(
                        "Repository admitted to ISOLATED LOCAL queue. "
                        "Use 'Run next queued (local Ollama)' below. "
                        "The stopped resident work session remains untouched.")
                elif outcome == "ran" and isinstance(data, dict):
                    self.portfolio_var.set(
                        f"Local source proposal: {data['state']} for "
                        f"{data['repository']}. Artifact: "
                        f"{data.get('proposal_path') or 'none'}")
                elif outcome == "error":
                    self.portfolio_var.set(
                        f"Local repository operation blocked: {data}. "
                        "Review the halted job; no automatic retry.")
                    self.inventory_var.set(f"Repository operation blocked: {data}")
            elif kind == "portfolio_error":
                self._portfolio_in_flight = False
                self.portfolio_var.set(str(payload))
            elif kind == "authority_result":
                self.approve_button.configure(state="normal")
                self.deny_button.configure(state="normal")
                self.status_var.set(str(payload))
                self.refresh_async()
        self.root.after(50, self._drain_events)

    def _render_response(self, result: dict[str, object]) -> None:
        response = str(result.get("response_text") or result.get("error") or result.get("state") or "No response text.")
        provider = str(result.get("provider") or "")
        selected_model = str(result.get("model_or_agent") or "unspecified")
        speaker = (
            f"Local Ollama model ({selected_model})"
            if provider.casefold() == "ollama"
            else "Selected runtime route"
        )
        self._append_conversation(speaker, response)
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

    def choose_profile(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="Choose portfolio profile",
                                          filetypes=(("Portfolio profile", "*.json"),))
        if path:
            self.portfolio_action("configure", profile_path=path)

    def choose_worker_pins(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select independently supplied worker digests",
            filetypes=(("Pinned SHA-256 JSON", "*.json"),))
        if not path:
            return
        try:
            digests = load_operator_pins(Path(path))
        except (ValueError, OSError) as exc:
            self.worker_pins_var.set(f"Pins rejected: {exc}")
            return
        self._operator_pins = digests
        self._worker_pins_file = Path(path)
        self.worker_pins_var.set(
            f"Pins selected: {Path(path).name} (consistency check, not trust proof)")

    def refresh_inventory(self) -> None:
        if self._closed or self._inventory_in_flight:
            return
        self._inventory_in_flight = True
        self.inventory_var.set("Syncing owned repositories from GitHub…")
        session_id = self.session_var.get().strip() or "portfolio"
        def work() -> None:
            try:
                result = self.view_model.owned_inventory(session_id=session_id)
                self._events.put(("inventory", result))
            except Exception as exc:
                self._events.put(("inventory_error",
                                   f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _render_inventory(self, result: dict[str, object]) -> None:
        rows = result.get("repositories")
        if not isinstance(rows, list):
            self.repository_tree.delete(*self.repository_tree.get_children())
            self.inventory_var.set("No authenticated inventory in the response.")
            return
        self.repository_tree.delete(*self.repository_tree.get_children())
        for repo in rows:
            if not isinstance(repo, dict):
                continue
            self.repository_tree.insert(
                "", "end",
                values=(repo.get("full_name") or repo.get("name") or "",
                        repo.get("visibility") or "UNKNOWN",
                        "ARCHIVED" if repo.get("archived") else "AVAILABLE",
                        repo.get("default_branch") or "—"))
        private = sum(isinstance(r,dict) and r.get("visibility")=="PRIVATE"
                      for r in rows)
        archived = sum(isinstance(r,dict) and bool(r.get("archived"))
                       for r in rows)
        mode = result.get("portfolio_source")
        origin = ("direct read-only GitHub" if mode=="LIVE_GITHUB_READ_ONLY_FRONTEND"
                  else "resident authenticated GitHub")
        self.inventory_var.set(
            f"{len(rows)} owned repositories  ·  {private} private  ·  "
            f"{archived} archived  ·  {origin}")

    def _render_local_jobs(self) -> None:
        self.local_jobs_tree.delete(*self.local_jobs_tree.get_children())
        jobs = self._repo_jobs.list_jobs()
        self._repo_job_rows.clear()
        for job in jobs:
            result = job["proposal_path"] or job["error"] or "Not run"
            row = self.local_jobs_tree.insert(
                "", "end",
                values=(job["repository"], job["head"][:10],
                        job["state"], str(result)[:135]))
            self._repo_job_rows[row] = job
        queue_count = sum(j["state"]=="QUEUED" for j in jobs)
        held_count = sum(j["state"]=="HALTED" for j in jobs)
        self.local_job_var.set(
            f"{len(jobs)} local jobs · {queue_count} queued · "
            f"{held_count} halted · separate from resident session")
        self.local_job_run_button.configure(
            state="disabled" if queue_count==0 or self._repo_job_in_flight
            else "normal")

    def view_selected_local_proposal(self) -> None:
        selection = self.local_jobs_tree.selection()
        if not selection:
            self.portfolio_var.set("Select a completed local proposal job first.")
            return
        job = self._repo_job_rows.get(selection[0])
        if not isinstance(job, dict) or job.get("state") != "AWAITING_REVIEW":
            self.portfolio_var.set("Selected job has no reviewable proposal.")
            return
        path = Path(str(job.get("proposal_path") or "")).resolve()
        if not path.is_relative_to(self._repo_jobs.root.resolve()):
            self.portfolio_var.set("Proposal location is outside the isolated job store.")
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["schema"] == "PORTAL_SOURCE_TREE_PROPOSAL_V1"
            assert data["repository"] == job["repository"]
            assert data["expected_head"] == job["head"]
            content = data["files"][0]["content"]
            assert isinstance(content, str) and len(content) <= 20000
        except (OSError, ValueError, KeyError, IndexError, TypeError, AssertionError):
            self.portfolio_var.set("Proposal failed readback validation.")
            return
        self._append_conversation(
            f"Local proposal · {job['repository']} · {job['head'][:10]}", content)
        self._inventory_notebook.select(self._conversation_tab)

    def queue_selected_repository(self) -> None:
        if self._closed or self._repo_job_in_flight:
            return
        selection = self.repository_tree.selection()
        if not selection:
            self.inventory_var.set("Choose one repository row before adding a job.")
            return
        repo = str(self.repository_tree.item(selection[0], "values")[0])
        self._repo_job_in_flight = True
        self.inventory_var.set(f"Validating {repo} against authenticated GitHub…")
        def work() -> None:
            try:
                token = _existing_github_token()
                if not token:
                    raise ValueError("authenticated GitHub connection unavailable")
                result=self._repo_jobs.enqueue(
                    repo,catalog=GitHubRepositoryCatalog(token=token))
                self._events.put(("local_job",("queued",result)))
            except Exception as exc:
                self._events.put(("local_job",("error",
                   f"{type(exc).__name__}: {exc}")))
        threading.Thread(target=work,daemon=True).start()

    def run_next_local_job(self) -> None:
        if self._closed or self._repo_job_in_flight:
            return
        self._repo_job_in_flight = True
        self.local_job_var.set("Running ONE queued job: isolated checkout + local Ollama…")
        self.local_job_run_button.configure(state="disabled")
        def work() -> None:
            try:
                token=_existing_github_token()
                if not token:
                    raise ValueError("authenticated GitHub connection unavailable")
                result=self._repo_jobs.run_one(
                    catalog=GitHubRepositoryCatalog(token=token))
                self._events.put(("local_job",("ran",result)))
            except Exception as exc:
                self._events.put(("local_job",("error",
                    f"{type(exc).__name__}: {exc}")))
        threading.Thread(target=work,daemon=True).start()

    def portfolio_action(self, action: str, **payload) -> None:
        if self._closed or self._portfolio_in_flight:
            return
        if action == "hold":
            selected = self.portfolio_tree.selection()
            if not selected:
                self.portfolio_var.set("Select a repository or work item to hold.")
                return
            payload.update(self._portfolio_subjects[selected[0]])
        session_id = self.session_var.get().strip() or "portfolio"
        if action in {"run", "continue"}:
            if self._last_portfolio is None:
                self.portfolio_var.set("Refresh the current work queue before dispatch.")
                return
            decision = prepare_portfolio_request(
                action, self._last_portfolio, pins=self._operator_pins)
            if not decision.allowed:
                self.portfolio_var.set(decision.explanation)
                return
            payload.update(decision.payload)
        self._portfolio_in_flight = True
        self.portfolio_var.set("Updating portfolio…")
        def work():
            try:
                result = self.view_model.portfolio(action, session_id=session_id, **payload)
                self._events.put(("portfolio", result))
            except Exception as exc:
                self._events.put(("portfolio_error", f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _portfolio_result_label(subject: dict[str, object]) -> str:
        verified = subject.get("verification_state")
        if isinstance(verified, str) and verified:
            return verified
        dispatch = subject.get("dispatch_state")
        if dispatch in {
            "FAILED_RETRYABLE", "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN", "FAILED_PRECONDITION",
            "FAILED_EXECUTION",
        }:
            return str(dispatch)
        return "Pending"

    def _render_portfolio(self, result: dict) -> None:
        session = result.get("session")
        self.portfolio_tree.delete(*self.portfolio_tree.get_children())
        self._portfolio_subjects.clear()

        self._last_portfolio = result
        source = str(result.get("portfolio_source") or "UNCONFIGURED")
        worker_state = str(result.get("worker_state") or "UNKNOWN")
        inventory = result.get("inventory")
        inventory_text = ""
        if isinstance(inventory, dict):
            inventory_text = (
                f" ? {inventory.get('public', 0)} public"
                f" ? {inventory.get('private', 0)} private"
                f" ? {inventory.get('archived', 0)} archived"
            )

        if not isinstance(session, dict):
            if result.get("configured"):
                self.portfolio_var.set(
                    f"{source}{inventory_text} ? worker {worker_state}\n"
                    + str(result.get("message", "Ready to run."))
                )
            else:
                self.portfolio_var.set(
                    "Ready. Run will connect the live portfolio automatically; "
                    "Advanced profile is optional."
                )
            return

        summary = session.get("summary", {})
        control = str(session.get("control_state") or "UNKNOWN")
        active, held, finished = (summary.get("active", 0),
                                   summary.get("held", 0),
                                   summary.get("terminal", 0))
        overview = (
            f"{control} · generation {session.get('generation')}  |  "
            f"{active} active · {held} held · {finished} finished\n"
            f"Worker: {worker_state}  |  Source: {source}")
        if control == "STOPPED":
            overview += ("\nQueue stopped. Do not press Run to replay held work. "
                         "New verified work must be admitted separately.")
        elif active == 0 and held:
            overview += ("\nNo currently runnable subjects. "
                         "Review held work before new admission.")
        else:
            overview += "\n" + str(result.get("message", ""))
        self.portfolio_var.set(overview)
        for item in session.get("subjects", []):
            route = item.get("route_id")
            if not route:
                route = (
                    "Worker unavailable"
                    if worker_state == "UNAVAILABLE"
                    else "Pending worker binding"
                )
            key = self.portfolio_tree.insert(
                "",
                "end",
                values=(
                    item.get("subject_id"),
                    item.get("state"),
                    route,
                    self._portfolio_result_label(item),
                ),
            )
            self._portfolio_subjects[key] = {
                "subject_id": item["subject_id"],
                "subject_kind": item["subject_kind"],
            }

    def run(self) -> None:
        self.message_entry.focus_set()
        self.root.mainloop()


def default_runtime_root() -> Path:
    configured = os.environ.get("VERA_RUNTIME_ROOT")
    if configured:
        return Path(configured)
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    candidates = []
    for base in (local_app_data / "VeraDesktopRuntime", local_app_data / "PortalVera", local_app_data / "P.O.R.T.A.L." / "runtimes"):
        if not base.is_dir():
            continue
        for runtime_root in base.iterdir():
            try:
                spec = json.loads((runtime_root / "RUNTIME_INSTALL_SPEC.json").read_text(encoding="utf-8-sig"))
                qualification = json.loads((runtime_root / "QUALIFICATION.json").read_text(encoding="utf-8-sig"))
                if (spec.get("activation", {}).get("active") is True
                        and spec.get("activation", {}).get("qualified") is True
                        and qualification.get("qualified") is True):
                    candidates.append((float(qualification.get("qualified_at", 0)), str(runtime_root), runtime_root))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    if candidates:
        # This chooses an installed control target, not proof it is running.
        # The supervisor separately verifies fresh heartbeat and live process.
        return max(candidates, key=lambda item: item[:2])[2]
    return local_app_data / "PortalVera" / "runtime"


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="P.O.R.T.A.L. Desktop — resident Vera interface")
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    parser.add_argument("--worker-pins", type=Path, default=None)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(argv)
    kwargs = {"runtime_root": args.runtime_root}
    if args.worker_pins is not None:
        kwargs["worker_pins_file"] = args.worker_pins
    if args.preview:
        kwargs["preview"] = True
    PortalDesktopApp(**kwargs).run()


if __name__ == "__main__":
    main()
