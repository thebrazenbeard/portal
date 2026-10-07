"""Native portfolio controls over Portal's existing durable command session."""
from __future__ import annotations

import json
from pathlib import Path

from runner.portfolio_wave_scheduler import WaveExecutionBudget
from .node_registry import load_execution_nodes
from .session import PortalCommandSession


class DesktopPortfolioController:
    def __init__(self, runtime_root: Path, *, session: PortalCommandSession):
        self.profile_path = Path(runtime_root) / "PORTFOLIO_PROFILE.json"
        self.session = session

    @staticmethod
    def _profile(payload: object, base: Path) -> dict:
        allowed = {"wave", "corpus", "projects", "nodes", "max_parallel", "holder"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported portfolio profile fields")
        profile = dict(payload)
        for field in ("wave", "corpus", "projects", "nodes"):
            value = profile.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"portfolio profile requires {field}")
            path = Path(value).expanduser()
            path = (base / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file():
                raise ValueError(f"{field} file is missing: {path}")
            profile[field] = str(path)
        parallel = profile.get("max_parallel", 4)
        if type(parallel) is not int or not 1 <= parallel <= 64:
            raise ValueError("max_parallel must be between 1 and 64")
        profile["max_parallel"] = parallel
        holder = profile.get("holder", "vera-desktop")
        if not isinstance(holder, str) or not holder.strip():
            raise ValueError("holder is required")
        profile["holder"] = holder.strip()
        return profile

    def handle(self, payload: dict) -> dict:
        action = payload.get("action", "status")
        if action == "configure":
            path = Path(str(payload.get("profile_path", ""))).expanduser().resolve()
            profile = self._profile(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.profile_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.profile_path)
        elif action not in {"status", "run", "continue", "hold", "stop"}:
            raise ValueError("unsupported portfolio control")
        session_id = str(payload.get("session_id") or "portfolio").strip()
        if not session_id or len(session_id) > 128:
            raise ValueError("session_id must contain 1 to 128 characters")
        profile = None
        profile_error = None
        if self.profile_path.is_file():
            try:
                profile = self._profile(
                    json.loads(self.profile_path.read_text(encoding="utf-8-sig")),
                    self.profile_path.parent,
                )
            except (OSError, ValueError) as exc:
                if action in {"configure", "run", "continue"}:
                    raise
                # The existing session remains the authority for monitoring and
                # no-refill intent when an old admission input disappears.
                profile_error = f"{type(exc).__name__}: {exc}"
        if action in {"run", "continue"}:
            if profile is None:
                raise ValueError("Choose a portfolio profile before starting a session")
            parallel = profile["max_parallel"]
            method = self.session.run if action == "run" else self.session.continue_run
            method(session_id=session_id, holder=profile["holder"],
                   wave_path=Path(profile["wave"]), corpus_path=Path(profile["corpus"]),
                   projects_path=Path(profile["projects"]), nodes=load_execution_nodes(Path(profile["nodes"])),
                   budget=WaveExecutionBudget(parallel, parallel, parallel), lease_ttl=300,
                   token=None, execution_adapter=None, public_safe=True)
        if action in {"hold", "stop"}:
            current = self.session.status(session_id)
            common = {"session_id": session_id, "holder": current["holder"]}
            if action == "stop":
                self.session.stop(**common)
            else:
                subject_id = payload.get("subject_id")
                if not isinstance(subject_id, str) or not subject_id.strip():
                    raise ValueError("select a portfolio subject to hold")
                self.session.hold(**common, subject_kind=str(payload.get("subject_kind") or "repository"), subject_id=subject_id)
        try:
            status = self.session.status(session_id)
        except ValueError:
            status = None
        rows = self.session.connection.execute("SELECT session_id FROM portal_command_sessions ORDER BY updated_at DESC LIMIT 50").fetchall()
        return {"schema": "PORTAL_DESKTOP_PORTFOLIO_V1", "configured": profile is not None,
                "profile": profile, "profile_error": profile_error,
                "session": status, "sessions": [str(row[0]) for row in rows],
                "dispatch_mode": "ADMISSION_ONLY", "protected_effect_authority": False,
                "message": "Queued work awaits an attached worker." +
                (f" Profile needs repair: {profile_error}" if profile_error else "")}
