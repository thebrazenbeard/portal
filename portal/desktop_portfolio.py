"""Native portfolio controls over Portal's durable command-session core."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable

import yaml

from runner.portfolio_wave_scheduler import WaveExecutionBudget
from runner.registry import load_project_snapshot

from .discovery import (
    GitHubRepositoryCatalog,
    build_live_project_registry,
    write_project_registry,
)
from .live_portfolio import refresh_live_local_portfolio
from .node_registry import load_execution_nodes
from .process_adapter import (
    PortalProposalProcessAdapter,
    build_process_proposal_execution_adapter,
)
from .resident_worker_trust import load_resident_backends
from .worker_registry import load_worker_backends
from .session import PortalCommandSession


ExecutableResolver = Callable[[str], str | None]
TokenProvider = Callable[[], str | None]


def _existing_github_token(
    *,
    executable_resolver: ExecutableResolver = shutil.which,
) -> str | None:
    """Use already-configured GitHub auth without persisting new credentials."""

    for name in (
        "PORTAL_GITHUB_TOKEN",
        "PROJECT_RUNNER_GITHUB_TOKEN",
        "GITHUB_TOKEN",
    ):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()

    gh = executable_resolver("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(
            (gh, "auth", "token"),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


class DesktopPortfolioController:
    """Desktop façade over the same durable Portal session machinery.

    Manual profiles remain supported, but a Run action with no profile now
    bootstraps a runtime-local live profile from the frozen Portal source.
    """

    def __init__(
        self,
        runtime_root: Path,
        *,
        session: PortalCommandSession,
        executable_resolver: ExecutableResolver = shutil.which,
        token_provider: TokenProvider | None = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.profile_path = self.runtime_root / "PORTFOLIO_PROFILE.json"
        self.session = session
        self.executable_resolver = executable_resolver
        self.token_provider = (
            token_provider
            if token_provider is not None
            else lambda: _existing_github_token(
                executable_resolver=self.executable_resolver
            )
        )
        self._last_inventory: dict[str, int] | None = None

    @property
    def _desktop_state_root(self) -> Path:
        return self.runtime_root / "state" / "portal" / "desktop"

    def _source_root(self) -> Path:
        installed = self.runtime_root / "sources" / "portal"
        if installed.is_dir():
            return installed
        return Path(__file__).resolve().parents[1]

    @staticmethod
    def _infer_owner(wave_path: Path) -> str:
        try:
            payload = json.loads(Path(wave_path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("cannot infer portfolio owner from baseline wave") from exc
        if not isinstance(payload, dict):
            raise ValueError("baseline wave must be an object")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("baseline wave items are required")
        owners: set[str] = set()
        canonical: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            repository = item.get("repository")
            if not isinstance(repository, str) or "/" not in repository:
                continue
            owner = repository.split("/", 1)[0].strip()
            if not owner:
                continue
            key = owner.casefold()
            owners.add(key)
            canonical.setdefault(key, owner)
        if len(owners) != 1:
            raise ValueError(
                "cannot infer one portfolio owner from baseline wave"
            )
        return canonical[next(iter(owners))]

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _save_profile(self, profile: dict[str, object]) -> None:
        self._write_json_atomic(self.profile_path, profile)

    @staticmethod
    def _profile(payload: object, base: Path) -> dict:
        allowed = {
            "mode",
            "wave",
            "corpus",
            "projects",
            "nodes",
            "max_parallel",
            "holder",
            "discover_owner",
            "discovered_effect_ceiling",
            "worker_backends",
            "workspace_root",
            "verifier",
            "worker_holder_prefix",
            "delivery_lease_ttl",
        }
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("unsupported portfolio profile fields")
        profile = dict(payload)

        mode = profile.get("mode", "MANUAL_V1")
        if mode not in {"MANUAL_V1", "LIVE_AUTO_V1"}:
            raise ValueError("unsupported portfolio profile mode")
        profile["mode"] = mode

        for field in ("wave", "corpus", "projects", "nodes"):
            value = profile.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"portfolio profile requires {field}")
            path = Path(value).expanduser()
            path = (
                (base / path).resolve()
                if not path.is_absolute()
                else path.resolve()
            )
            if not path.is_file():
                raise ValueError(f"{field} file is missing: {path}")
            profile[field] = str(path)

        optional_file = profile.get("worker_backends")
        if optional_file is not None:
            if not isinstance(optional_file, str) or not optional_file.strip():
                raise ValueError("worker_backends must be null or a path")
            path = Path(optional_file).expanduser()
            path = (
                (base / path).resolve()
                if not path.is_absolute()
                else path.resolve()
            )
            if not path.is_file():
                raise ValueError(f"worker_backends file is missing: {path}")
            profile["worker_backends"] = str(path)
        else:
            profile["worker_backends"] = None

        workspace = profile.get("workspace_root")
        if workspace is None:
            workspace_path = base / "workers"
        elif isinstance(workspace, str) and workspace.strip():
            workspace_path = Path(workspace).expanduser()
            workspace_path = (
                (base / workspace_path).resolve()
                if not workspace_path.is_absolute()
                else workspace_path.resolve()
            )
        else:
            raise ValueError("workspace_root must be a path")
        profile["workspace_root"] = str(workspace_path)

        parallel = profile.get("max_parallel", 4)
        if type(parallel) is not int or not 1 <= parallel <= 64:
            raise ValueError("max_parallel must be between 1 and 64")
        profile["max_parallel"] = parallel

        holder = profile.get("holder", "vera-desktop")
        if not isinstance(holder, str) or not holder.strip():
            raise ValueError("holder is required")
        profile["holder"] = holder.strip()

        owner = profile.get("discover_owner")
        if owner is not None:
            if not isinstance(owner, str) or not owner.strip():
                raise ValueError(
                    "discover_owner must be null or a non-empty string"
                )
            profile["discover_owner"] = owner.strip()

        ceiling = profile.get("discovered_effect_ceiling", "NO_EFFECT")
        if ceiling not in {"NO_EFFECT", "SOURCE_ONLY"}:
            raise ValueError(
                "discovered_effect_ceiling must be NO_EFFECT or SOURCE_ONLY"
            )
        profile["discovered_effect_ceiling"] = ceiling

        verifier = profile.get("verifier", "vera-review")
        if not isinstance(verifier, str) or not verifier.strip():
            raise ValueError("verifier is required")
        profile["verifier"] = verifier.strip()

        holder_prefix = profile.get(
            "worker_holder_prefix",
            "desktop-proposal",
        )
        if not isinstance(holder_prefix, str) or not holder_prefix.strip():
            raise ValueError("worker_holder_prefix is required")
        profile["worker_holder_prefix"] = holder_prefix.strip()

        delivery_ttl = profile.get("delivery_lease_ttl", 900.0)
        if (
            isinstance(delivery_ttl, bool)
            or not isinstance(delivery_ttl, (int, float))
            or float(delivery_ttl) <= 0
        ):
            raise ValueError("delivery_lease_ttl must be positive")
        profile["delivery_lease_ttl"] = float(delivery_ttl)
        return profile

    def _missing_worker_tools(self) -> list[str]:
        return [
            name
            for name in ("codex", "gh", "git")
            if not self.executable_resolver(name)
        ]

    def _bootstrap_live_profile(
        self,
        *,
        owner: str | None = None,
        max_parallel: int = 1,
    ) -> dict:
        source = self._source_root()
        wave = source / "portfolio" / "advancement_wave.public.json"
        corpus = source / "portfolio" / "corpus.public.json"
        projects = source / "registry" / "projects.yaml"
        for label, path in (
            ("wave", wave),
            ("corpus", corpus),
            ("projects", projects),
        ):
            if not path.is_file():
                raise ValueError(
                    f"installed Portal source is missing {label}: {path}"
                )

        if owner is None:
            owner = self._infer_owner(wave)
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("portfolio owner is required")
        owner = owner.strip()

        state = self._desktop_state_root
        state.mkdir(parents=True, exist_ok=True)
        node_id = "desktop-local"
        nodes_path = state / "nodes.live.yaml"
        nodes_payload = {
            "schema": "PORTAL_EXECUTION_NODES_V1",
            "nodes": [
                {
                    "id": node_id,
                    "max_parallel": int(max_parallel),
                    "allowed_lanes": [],
                    "enabled": True,
                }
            ],
        }
        nodes_path.write_text(
            yaml.safe_dump(nodes_payload, sort_keys=False),
            encoding="utf-8",
        )

        executables = {
            name: self.executable_resolver(name)
            for name in ("codex", "gh", "git")
        }
        missing = sorted(
            name for name, value in executables.items() if not value
        )
        worker_path: Path | None = None
        if not missing:
            worker_path = state / "worker-backends.live.yaml"
            worker_payload = {
                "schema": "PORTAL_WORKER_BACKENDS_V1",
                "workers": [
                    {
                        "node_id": node_id,
                        "kind": "CODEX_GH_PROPOSAL_V1",
                        "timeout_seconds": 840,
                        # The generic process sandbox remains minimal. Only
                        # this authenticated GitHub proposal worker receives
                        # the path to existing CLI credential configuration.
                        # These are paths/config selectors, not token values.
                        "pass_env": (
                            ["APPDATA", "GH_CONFIG_DIR"]
                            if os.name == "nt"
                            else ["GH_CONFIG_DIR", "XDG_CONFIG_HOME"]
                        ),
                        "codex": str(Path(str(executables["codex"])).resolve()),
                        "gh": str(Path(str(executables["gh"])).resolve()),
                        "git": str(Path(str(executables["git"])).resolve()),
                    }
                ],
            }
            worker_path.write_text(
                yaml.safe_dump(worker_payload, sort_keys=False),
                encoding="utf-8",
            )

        profile = self._profile(
            {
                "mode": "LIVE_AUTO_V1",
                "wave": str(wave),
                "corpus": str(corpus),
                "projects": str(projects),
                "nodes": str(nodes_path),
                "max_parallel": int(max_parallel),
                "holder": "vera-desktop",
                "discover_owner": owner,
                # SOURCE_ONLY admits proposal generation only. The process
                # worker still binds execution/source/target-ref mutation to false,
                # so publication remains behind the separate promotion gate.
                "discovered_effect_ceiling": "SOURCE_ONLY",
                "worker_backends": (
                    str(worker_path) if worker_path is not None else None
                ),
                "workspace_root": str(state / "workers"),
                "verifier": "vera-review",
                "worker_holder_prefix": "desktop-proposal",
                "delivery_lease_ttl": 900.0,
            },
            self.runtime_root,
        )
        self._save_profile(profile)
        return profile

    def _refresh_live_inputs(
        self,
        profile: dict,
    ) -> tuple[Path, Path, Path, dict[str, int]]:
        owner = profile.get("discover_owner")
        if not owner:
            return (
                Path(profile["wave"]),
                Path(profile["corpus"]),
                Path(profile["projects"]),
                {},
            )

        token = self.token_provider()
        if profile.get("mode") == "LIVE_AUTO_V1" and not token:
            raise ValueError(
                "live-auto portfolio requires authenticated GitHub access; "
                "public-only discovery cannot stand in for the owned inventory"
            )
        repositories = GitHubRepositoryCatalog(token=token).list_owned_repositories(
            str(owner)
        )
        curated = load_project_snapshot(Path(profile["projects"]))
        registry = build_live_project_registry(
            owner=str(owner),
            repositories=repositories,
            curated_projects=curated.projects,
        )
        state = self._desktop_state_root
        live_registry = state / "projects.live.yaml"
        write_project_registry(live_registry, registry)
        live = refresh_live_local_portfolio(
            baseline_corpus_path=Path(profile["corpus"]),
            baseline_wave_path=Path(profile["wave"]),
            repositories=repositories,
            observed_at=datetime.now(timezone.utc).isoformat(),
            output_dir=state / "live-portfolio",
            discovered_effect_ceiling=profile["discovered_effect_ceiling"],
        )
        inventory = {
            "public": live.public_repositories,
            "private": live.private_repositories,
            "archived": live.archived_repositories,
        }
        self._last_inventory = inventory
        return live.wave_path, live.corpus_path, live_registry, inventory

    def _execution_adapter(
        self,
        profile: dict,
        nodes,
        token: str | None,
        *,
        pins: dict | None = None,
    ):
        worker_backends = profile.get("worker_backends")
        if worker_backends is None:
            return None
        backends = load_resident_backends(
            Path(worker_backends),
            nodes=tuple(nodes),
            pins=pins,
            live_auto=profile.get("mode") == "LIVE_AUTO_V1",
        )
        driver = PortalProposalProcessAdapter(
            state_db=self.session.path,
            nodes=tuple(nodes),
            backends=backends,
            workspace_root=Path(profile["workspace_root"]),
            holder_prefix=profile["worker_holder_prefix"],
            delivery_lease_ttl=float(profile["delivery_lease_ttl"]),
            token=token,
        )
        return build_process_proposal_execution_adapter(driver)

    def _load_profile(
        self,
        *,
        required_for_action: bool,
    ) -> tuple[dict | None, str | None]:
        if not self.profile_path.is_file():
            return None, None
        try:
            return (
                self._profile(
                    json.loads(
                        self.profile_path.read_text(encoding="utf-8-sig")
                    ),
                    self.profile_path.parent,
                ),
                None,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if required_for_action:
                raise
            return None, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _capacity_snapshot(
        profile: dict | None, status: dict | None, worker_configured: bool,
    ) -> dict:
        """Read-only capacity diagnosis, not worker dispatch qualification."""
        requested = profile["max_parallel"] if profile is not None else 0
        enabled_slots = 0
        enabled_node_ids: tuple[str, ...] = ()
        reason = None
        if profile is not None:
            try:
                nodes = load_execution_nodes(Path(profile["nodes"]))
                enabled_node_ids = tuple(
                    node.node_id for node in nodes if node.enabled
                )
                enabled_slots = sum(
                    node.max_parallel for node in nodes if node.enabled
                )
            except (OSError, ValueError, yaml.YAMLError):
                reason = "NODE_CONFIG_UNQUALIFIED"
        control = status.get("control_state") if status else None
        blocked = []
        if control != "RUNNING":
            blocked.append(
                "STOPPED_SESSION_REQUIRES_RECONCILIATION"
                if control == "STOPPED" else "NO_RUNNING_SESSION"
            )
        if not worker_configured:
            blocked.append("WORKER_NOT_CONFIGURED")
        if enabled_slots == 0:
            blocked.append(reason or "NO_ENABLED_NODE_SLOTS")
        if worker_configured and profile is not None:
            try:
                backends = load_worker_backends(
                    Path(profile["worker_backends"])
                )
                if any(node_id not in backends for node_id in enabled_node_ids):
                    blocked.append("MISSING_ENABLED_WORKER_BACKEND")
            except (OSError, ValueError, yaml.YAMLError):
                blocked.append("WORKER_BACKEND_CONFIG_UNQUALIFIED")
        return {
            "profile_max_parallel": requested,
            "enabled_node_slots": enabled_slots,
            "advisory_slot_ceiling": min(requested, enabled_slots),
            "session_state": control,
            "preflight_blockers": blocked,
            # An inspection never certifies a worker or promotes effects.
            "worker_execution_verified": False,
            "protected_effect_authority": False,
        }

    def handle(self, payload: dict) -> dict:
        action = payload.get("action", "status")
        if action == "configure":
            path = (
                Path(str(payload.get("profile_path", "")))
                .expanduser()
                .resolve()
            )
            profile = self._profile(
                json.loads(path.read_text(encoding="utf-8-sig")),
                path.parent,
            )
            self._save_profile(profile)
        elif action == "bootstrap_live":
            self._bootstrap_live_profile(
                owner=(
                    str(payload["owner"]).strip()
                    if payload.get("owner") is not None
                    else None
                ),
                max_parallel=int(payload.get("max_parallel", 1)),
            )
        elif action not in {"status", "inspect", "run", "continue", "hold", "stop"}:
            raise ValueError("unsupported portfolio control")

        session_id = str(
            payload.get("session_id") or "portfolio"
        ).strip()
        if not session_id or len(session_id) > 128:
            raise ValueError("session_id must contain 1 to 128 characters")

        profile, profile_error = self._load_profile(
            required_for_action=action in {"configure", "run", "continue"}
        )

        if (action == "continue" and profile is None
                and payload.get("expected_generation") is not None):
            raise ValueError("pinned continuation requires an existing profile")
        if action in {"run", "continue"} and profile is None:
            profile = self._bootstrap_live_profile()
            profile_error = None

        if action == "continue" and payload.get("expected_generation") is not None:
            expected_generation = payload["expected_generation"]
            if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
                raise ValueError("expected_generation must be an integer")
            current = self.session.status(session_id)
            if (current["control_state"] != "RUNNING"
                    or current["generation"] != expected_generation):
                raise ValueError("stale portfolio generation; no new work admitted")
            expected_holder = payload.get("expected_holder")
            if expected_holder is not None and current["holder"] != expected_holder:
                raise ValueError("portfolio holder changed; no new work admitted")

        if action in {"run", "continue"}:
            assert profile is not None
            wave_path, corpus_path, projects_path, inventory = (
                self._refresh_live_inputs(profile)
            )
            nodes = load_execution_nodes(Path(profile["nodes"]))
            token = (
                self.token_provider()
                if profile.get("mode") == "LIVE_AUTO_V1"
                else None
            )
            execution_adapter = self._execution_adapter(
                profile,
                nodes,
                token,
                pins=payload,
            )
            parallel = profile["max_parallel"]
            budget = WaveExecutionBudget(parallel, parallel, parallel)
            common = dict(
                session_id=session_id,
                holder=profile["holder"],
                wave_path=wave_path,
                corpus_path=corpus_path,
                projects_path=projects_path,
                nodes=nodes,
                budget=budget,
                lease_ttl=300,
                token=token,
                execution_adapter=execution_adapter,
                public_safe=not bool(profile.get("discover_owner")),
            )
            if action == "run":
                self.session.run(**common)
            else:
                self.session.continue_run(
                    **common,
                    verifier=profile["verifier"],
                )
            self._last_inventory = inventory or self._last_inventory

        if action in {"hold", "stop"}:
            current = self.session.status(session_id)
            common = {
                "session_id": session_id,
                "holder": current["holder"],
            }
            if action == "stop":
                self.session.stop(**common)
            else:
                subject_id = payload.get("subject_id")
                if not isinstance(subject_id, str) or not subject_id.strip():
                    raise ValueError(
                        "select a portfolio subject to hold"
                    )
                self.session.hold(
                    **common,
                    subject_kind=str(
                        payload.get("subject_kind") or "repository"
                    ),
                    subject_id=subject_id,
                )

        try:
            status = self.session.status(session_id)
        except ValueError:
            status = None

        rows = self.session.connection.execute(
            """
            SELECT session_id
            FROM portal_command_sessions
            ORDER BY updated_at DESC
            LIMIT 50
            """
        ).fetchall()

        missing_tools = self._missing_worker_tools()
        worker_configured = bool(
            profile is not None and profile.get("worker_backends")
        )
        portfolio_source = (
            "LIVE_GITHUB"
            if profile is not None and profile.get("discover_owner")
            else "STATIC_PROFILE"
        )
        if worker_configured:
            dispatch_mode = "PROCESS_PROPOSAL"
            worker_state = "CONFIGURED"
            message = (
                "Live portfolio connected. Advisory source-proposal worker "
                "is configured; publication still requires separate governed "
                "promotion/authority."
            )
        else:
            dispatch_mode = "ADMISSION_ONLY"
            worker_state = "UNAVAILABLE"
            missing_text = ", ".join(missing_tools) or "worker backend"
            message = (
                "Portfolio connected. No executable proposal worker is "
                f"available ({missing_text})."
            )
        if profile_error:
            message += f" Profile needs repair: {profile_error}"

        return {
            "schema": "PORTAL_DESKTOP_PORTFOLIO_V2",
            "configured": profile is not None,
            "profile": profile,
            "profile_error": profile_error,
            "session": status,
            "sessions": [str(row[0]) for row in rows],
            "portfolio_source": portfolio_source,
            "inventory": self._last_inventory,
            "dispatch_mode": dispatch_mode,
            "worker_state": worker_state,
            "capacity": self._capacity_snapshot(profile, status, worker_configured),
            "missing_worker_tools": missing_tools,
            "protected_effect_authority": False,
            "message": message,
        }
