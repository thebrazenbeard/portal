from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import threading
import time
from typing import Protocol
import uuid

from .desktop_authority import DesktopAuthorityService
from .desktop_ipc import FileBridgeClient
from .desktop_state import (
    DesktopStateStore,
    load_pending_authority_requests,
    load_portfolio_view,
)
from .desktop_supervisor import (
    RuntimeSupervisor,
    RuntimeSupervisorConfig,
    discover_active_runtime_root,
)


def resolve_runtime_root(
    *,
    configured: str | None,
    local_app_data: Path | None = None,
) -> Path:
    if configured is not None and configured.strip():
        return Path(configured).expanduser().resolve()

    if local_app_data is None:
        raw_local = os.environ.get("LOCALAPPDATA")
        local_app_data = (
            Path(raw_local)
            if raw_local
            else Path.home() / "AppData" / "Local"
        )
    runtime_root = discover_active_runtime_root(
        Path(local_app_data) / "VeraDesktopRuntime"
    )
    if runtime_root is None:
        raise FileNotFoundError(
            "no qualified active VeraDesktopRuntime installation was found"
        )
    return runtime_root


class RuntimeClient(Protocol):
    def request(self, command: str, **payload: object) -> dict[str, object]:
        ...


class AuthorityService(Protocol):
    def approve(
        self,
        request_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        ...

    def deny(
        self,
        request_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class DesktopSnapshot:
    runtime_state: str
    runtime_id: str | None
    current_route: str | None
    last_autonomous_activity: dict[str, object] | None
    recent_activity: tuple[dict[str, object], ...]
    pending_effects: tuple[dict[str, object], ...]
    conversation: tuple[dict[str, object], ...]
    open_loops: tuple[dict[str, object], ...]
    portfolio_observed_at: str | None
    portfolio_descriptive_only: bool
    portfolio_authority_granted: bool


class DesktopViewModel:
    def __init__(
        self,
        client: RuntimeClient,
        *,
        state_store: DesktopStateStore | None = None,
        portfolio_path: Path | None = None,
        authority_requests_path: Path | None = None,
        authority_service: AuthorityService | None = None,
    ) -> None:
        self.client = client
        self.state_store = state_store
        self.authority_service = authority_service
        self.portfolio_path = (
            Path(portfolio_path) if portfolio_path is not None else None
        )
        self.authority_requests_path = (
            Path(authority_requests_path)
            if authority_requests_path is not None
            else None
        )

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
        if self.state_store is None:
            conversation: tuple[dict[str, object], ...] = ()
        else:
            conversation = self.state_store.list_messages(limit=200)

        portfolio = {
            "observed_at": None,
            "descriptive_only": True,
            "authority_granted": False,
            "open_loops": (),
        }
        if self.portfolio_path is not None and self.portfolio_path.is_file():
            portfolio = load_portfolio_view(self.portfolio_path)

        pending_effects = (
            load_pending_authority_requests(self.authority_requests_path)
            if self.authority_requests_path is not None
            else ()
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
            pending_effects=pending_effects,
            conversation=conversation,
            open_loops=tuple(portfolio["open_loops"]),
            portfolio_observed_at=(
                str(portfolio["observed_at"])
                if portfolio.get("observed_at") is not None
                else None
            ),
            portfolio_descriptive_only=bool(portfolio["descriptive_only"]),
            portfolio_authority_granted=bool(portfolio["authority_granted"]),
        )

    def approve_authority(
        self,
        request_id: str,
        *,
        now: float | None = None,
        valid_for_seconds: float = 300.0,
    ) -> dict[str, object]:
        if self.authority_service is None:
            raise RuntimeError("desktop authority service is unavailable")
        return self.authority_service.approve(
            request_id,
            now=float(time.time() if now is None else now),
            valid_for_seconds=valid_for_seconds,
        )

    def deny_authority(
        self,
        request_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, object]:
        if self.authority_service is None:
            raise RuntimeError("desktop authority service is unavailable")
        return self.authority_service.deny(
            request_id,
            now=float(time.time() if now is None else now),
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
        resolved_request_id = request_id or uuid.uuid4().hex
        resolved_created_at = float(
            time.time() if created_at is None else created_at
        )
        if self.state_store is not None:
            self.state_store.append_message(
                role="human",
                content=task,
                created_at=resolved_created_at,
                request_id=resolved_request_id,
            )
        result = self.client.request(
            "desktop_cognize",
            request_id=resolved_request_id,
            source="HUMAN",
            reason="desktop conversation message",
            task=task,
            created_at=resolved_created_at,
            required_capabilities=["text"],
        )
        if self.state_store is not None:
            response_text = (
                result.get("response_text")
                or result.get("error")
                or result.get("state")
                or "No response text."
            )
            self.state_store.append_message(
                role="vera",
                content=str(response_text),
                created_at=time.time(),
                request_id=resolved_request_id,
                state=(
                    str(result["state"])
                    if result.get("state") is not None
                    else None
                ),
                route_id=(
                    str(result["route_id"])
                    if result.get("route_id") is not None
                    else None
                ),
            )
        return result


class PortalDesktopApp:
    def __init__(self, *, runtime_root: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("P.O.R.T.A.L. Desktop — Vera")
        self.root.geometry("1120x760")
        self.root.minsize(900, 620)

        self.runtime_root = Path(runtime_root).resolve()
        self.supervisor = RuntimeSupervisor(
            RuntimeSupervisorConfig(runtime_root=self.runtime_root)
        )
        try:
            bootstrap_status = self.supervisor.ensure_started()
            self._bootstrap_status = (
                f"{bootstrap_status.state.value}: {bootstrap_status.reason}"
            )
        except Exception as exc:
            self._bootstrap_status = f"{type(exc).__name__}: {exc}"

        self.client = FileBridgeClient(
            self.runtime_root,
            timeout_seconds=180.0,
        )
        self.state_store = DesktopStateStore(
            self.runtime_root / "state" / "desktop" / "desktop-ui.sqlite3"
        )
        self.authority_service = DesktopAuthorityService(self.runtime_root)
        self.view_model = DesktopViewModel(
            self.client,
            state_store=self.state_store,
            authority_service=self.authority_service,
            portfolio_path=(
                self.runtime_root
                / "sources"
                / "portal"
                / "portfolio"
                / "corpus.public.json"
            ),
            authority_requests_path=(
                self.runtime_root
                / "state"
                / "portal"
                / "authority-requests"
            ),
        )
        self.root.protocol("WM_DELETE_WINDOW", self._close)

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

        ttk.Label(operations_frame, text="Projects / open loops").pack(
            anchor="w"
        )
        self.portfolio_var = tk.StringVar(
            value="Portfolio: descriptive snapshot only."
        )
        ttk.Label(
            operations_frame,
            textvariable=self.portfolio_var,
            wraplength=360,
        ).pack(fill="x", pady=(2, 2))
        self.open_loops_text = tk.Text(
            operations_frame,
            height=8,
            wrap="word",
            state="disabled",
        )
        self.open_loops_text.pack(fill="x", pady=(2, 8))

        ttk.Label(operations_frame, text="Activity").pack(anchor="w")
        self.activity_text = tk.Text(
            operations_frame,
            height=12,
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
        ).pack(fill="x", pady=(2, 2))
        self.effects_list = tk.Listbox(
            operations_frame,
            height=6,
            exportselection=False,
        )
        self.effects_list.pack(fill="x", pady=(2, 4))
        self._pending_effects: tuple[dict[str, object], ...] = ()

        effect_buttons = ttk.Frame(operations_frame)
        effect_buttons.pack(fill="x")
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

        self.status_var = tk.StringVar(
            value=f"Supervisor: {self._bootstrap_status}"
        )
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

        conversation = "".join(
            (
                f"{'Patrick' if item.get('role') == 'human' else 'Vera'}: "
                f"{item.get('content', '')}\n\n"
            )
            for item in snapshot.conversation
        )
        if conversation:
            self._set_text(self.conversation, conversation)
            self.conversation.see("end")

        observed = snapshot.portfolio_observed_at or "unknown observation time"
        self.portfolio_var.set(
            f"Portfolio snapshot: {observed} · descriptive only · "
            f"authority={snapshot.portfolio_authority_granted}"
        )
        open_loops = "\n\n".join(
            (
                f"[{item.get('priority') or '-'}] {item.get('name')} "
                f"({item.get('id')})\n{item.get('frontier')}"
            )
            for item in snapshot.open_loops[:20]
        ) or "No active frontiers in the staged descriptive corpus."
        self._set_text(self.open_loops_text, open_loops)

        activity = "\n".join(
            self._activity_line(item)
            for item in snapshot.recent_activity[:30]
        ) or "No cognition activity recorded."
        self._set_text(self.activity_text, activity)

        self._pending_effects = snapshot.pending_effects
        self.effects_list.delete(0, "end")
        for item in snapshot.pending_effects:
            self.effects_list.insert(
                "end",
                (
                    f"{item.get('effect_class')} · {item.get('target')} · "
                    f"{item.get('summary')}"
                ),
            )
        self.effects_var.set(
            "None. Cognition carries no protected-effect authority."
            if not snapshot.pending_effects
            else (
                f"{len(snapshot.pending_effects)} pending exact authority "
                "request(s). Approve mints Project Runner grants; it does not "
                "itself execute the effect."
            )
        )

    def _selected_authority_request_id(self) -> str:
        selection = self.effects_list.curselection()
        if not selection:
            raise ValueError("select a pending authority request first")
        index = int(selection[0])
        if index < 0 or index >= len(self._pending_effects):
            raise ValueError("selected authority request is stale")
        request_id = self._pending_effects[index].get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("selected authority request has no request id")
        return request_id

    def approve_selected_authority(self) -> None:
        from tkinter import messagebox

        try:
            request_id = self._selected_authority_request_id()
            item = next(
                item
                for item in self._pending_effects
                if item.get("request_id") == request_id
            )
        except Exception as exc:
            self.status_var.set(f"Authority: {type(exc).__name__}: {exc}")
            return

        approved = messagebox.askyesno(
            "Approve exact authority",
            (
                f"{item.get('effect_class')}\n"
                f"{item.get('target')}\n\n"
                f"{item.get('summary')}\n\n"
                "This mints fresh Project Runner execution authority"
                + (
                    " and protected-effect authority."
                    if item.get(
                        "approval_mints_protected_effect_authority"
                    )
                    else "."
                )
                + " It does not bypass Project Runner review, fencing, "
                "currentness, or backend checks."
            ),
            parent=self.root,
        )
        if not approved:
            return

        self.status_var.set("Minting exact authority grants…")
        self.approve_button.configure(state="disabled")
        self.deny_button.configure(state="disabled")

        def work() -> None:
            try:
                result = self.view_model.approve_authority(request_id)
                message = (
                    f"APPROVED {request_id}; grants written; "
                    f"execution_performed={result.get('execution_performed')}"
                )
            except Exception as exc:
                message = f"Authority approval failed: {type(exc).__name__}: {exc}"

            def finish() -> None:
                self.status_var.set(message)
                self.approve_button.configure(state="normal")
                self.deny_button.configure(state="normal")
                self.refresh_async()

            self.root.after(0, finish)

        threading.Thread(target=work, daemon=True).start()

    def deny_selected_authority(self) -> None:
        try:
            request_id = self._selected_authority_request_id()
            result = self.view_model.deny_authority(request_id)
            self.status_var.set(
                f"DENIED {request_id}; execution_performed="
                f"{result.get('execution_performed')}"
            )
        except Exception as exc:
            self.status_var.set(f"Authority denial failed: {type(exc).__name__}: {exc}")
        self.refresh_async()

    def _close(self) -> None:
        try:
            self.state_store.close()
        finally:
            self.root.destroy()

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
        message = self.message_var.get().strip()
        if not message:
            return
        self.message_var.set("")
        self._append_conversation("Patrick", message)
        self.status_var.set("Vera is thinking…")
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
    runtime_root = resolve_runtime_root(
        configured=os.environ.get("VERA_RUNTIME_ROOT"),
    )
    PortalDesktopApp(runtime_root=runtime_root).run()


if __name__ == "__main__":
    main()
