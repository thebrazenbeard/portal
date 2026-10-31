from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping


class WorkerLifecycle(str, Enum):
    REGISTERED = "REGISTERED"
    DISCOVERED = "DISCOVERED"
    PROFILED = "PROFILED"
    CONNECTED = "CONNECTED"
    EXECUTABLE = "EXECUTABLE"
    UNAVAILABLE = "UNAVAILABLE"


class RouteState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    CONNECTED = "CONNECTED"
    VERIFIED = "VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class RouteEffectClass(str, Enum):
    READ_ONLY = "READ_ONLY"
    MUTATING = "MUTATING"


class ReplayPolicy(str, Enum):
    SAFE = "SAFE"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    NEVER = "NEVER"


@dataclass(frozen=True)
class WorkerRouteContract:
    effect_class: RouteEffectClass
    replay_policy: ReplayPolicy

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "WorkerRouteContract":
        return cls(
            effect_class=RouteEffectClass(str(data["effect_class"])),
            replay_policy=ReplayPolicy(str(data["replay_policy"])),
        )


class WorkerType(str, Enum):
    CHATGPT_CUSTOM_GPT = "CHATGPT_CUSTOM_GPT"
    CHATGPT_PLUGIN = "CHATGPT_PLUGIN"
    OPENAI_AGENT = "OPENAI_AGENT"
    GITHUB_ACTION = "GITHUB_ACTION"
    EXTERNAL_API = "EXTERNAL_API"
    HUMAN = "HUMAN"


class InvocationRoute(str, Enum):
    CHATGPT_INVOCATION = "CHATGPT_INVOCATION"
    RUNNER_ACTION_PULL = "RUNNER_ACTION_PULL"
    DIRECT_EXTERNAL_DISPATCH = "DIRECT_EXTERNAL_DISPATCH"
    PLUGIN_TOOL_CALL = "PLUGIN_TOOL_CALL"
    OPENAI_AGENT_API = "OPENAI_AGENT_API"
    GITHUB_ACTION = "GITHUB_ACTION"
    HUMAN_MANUAL = "HUMAN_MANUAL"


_GPT_ID_RE = re.compile(r"^g-[A-Za-z0-9]+$")
_REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


@dataclass(frozen=True)
class WorkerDefinition:
    id: str
    name: str
    worker_type: WorkerType
    lifecycle: WorkerLifecycle
    locators: Mapping[str, str]
    roles: tuple[str, ...]
    routes: Mapping[InvocationRoute, RouteState]
    route_contracts: Mapping[InvocationRoute, WorkerRouteContract]
    reconstruction: Mapping[str, str] | None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "WorkerDefinition":
        worker_type = WorkerType(str(data["worker_type"]))
        lifecycle = WorkerLifecycle(str(data["lifecycle"]))
        raw_locators = data.get("locators", {})
        raw_routes = data.get("routes", {})
        raw_route_contracts = data.get("route_contracts", {})
        raw_reconstruction = data.get("reconstruction")
        if not isinstance(raw_locators, Mapping):
            raise ValueError("locators must be a mapping")
        if not isinstance(raw_routes, Mapping):
            raise ValueError("routes must be a mapping")
        if not isinstance(raw_route_contracts, Mapping):
            raise ValueError("route_contracts must be a mapping")

        locators = {str(k): str(v) for k, v in raw_locators.items()}

        reconstruction = None
        if raw_reconstruction is not None:
            if not isinstance(raw_reconstruction, Mapping):
                raise ValueError("reconstruction must be a mapping")
            if set(raw_reconstruction) != {"repository", "path", "commit"}:
                raise ValueError(
                    "reconstruction requires exactly repository, path, and commit"
                )
            reconstruction = {
                key: str(raw_reconstruction[key]).strip()
                for key in ("repository", "path", "commit")
            }
            if _REPOSITORY_RE.fullmatch(reconstruction["repository"]) is None:
                raise ValueError("reconstruction repository must be owner/repo without whitespace")
            if not reconstruction["path"]:
                raise ValueError("reconstruction path is required")
            if re.fullmatch(r"[0-9a-f]{40}", reconstruction["commit"]) is None:
                raise ValueError("reconstruction commit must be exact 40-hex Git commit")

        if worker_type is WorkerType.CHATGPT_CUSTOM_GPT:
            gpt_id = locators.get("gpt_id")
            if gpt_id is None or _GPT_ID_RE.fullmatch(gpt_id) is None:
                raise ValueError("GPT id must match ^g-[A-Za-z0-9]+$")

        routes = {
            InvocationRoute(str(route)): RouteState(str(state))
            for route, state in raw_routes.items()
        }
        route_contracts: dict[InvocationRoute, WorkerRouteContract] = {}
        for route, raw_contract in raw_route_contracts.items():
            invocation_route = InvocationRoute(str(route))
            if invocation_route not in routes:
                raise ValueError("route contract requires a declared worker route")
            if not isinstance(raw_contract, Mapping):
                raise ValueError("route contract must be a mapping")
            route_contracts[invocation_route] = WorkerRouteContract.from_mapping(
                raw_contract
            )
        route_states = set(routes.values())
        if lifecycle is WorkerLifecycle.CONNECTED and not (
            RouteState.CONNECTED in route_states or RouteState.VERIFIED in route_states
        ):
            raise ValueError("CONNECTED worker requires a connected or verified route")
        if lifecycle is WorkerLifecycle.EXECUTABLE and RouteState.VERIFIED not in route_states:
            raise ValueError("EXECUTABLE worker requires at least one VERIFIED route")
        if lifecycle in {
            WorkerLifecycle.PROFILED,
            WorkerLifecycle.CONNECTED,
            WorkerLifecycle.EXECUTABLE,
        } and reconstruction is None:
            raise ValueError(
                f"{lifecycle.value} worker requires durable reconstruction evidence"
            )

        raw_roles = data.get("roles", [])
        if not isinstance(raw_roles, list):
            raise ValueError("roles must be a list")

        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            worker_type=worker_type,
            lifecycle=lifecycle,
            locators=locators,
            roles=tuple(str(role) for role in raw_roles),
            routes=routes,
            route_contracts=route_contracts,
            reconstruction=reconstruction,
        )


class ProjectAssignmentScope(str, Enum):
    NONE = "NONE"
    BT2_ASSIGNMENT = "BT2_ASSIGNMENT"
    EXTERNAL_BOUNDED = "EXTERNAL_BOUNDED"


class ProjectReviewScope(str, Enum):
    NONE = "NONE"
    STANDING = "STANDING"


@dataclass(frozen=True)
class ProjectExecutionTarget:
    work_type: str
    repository: str
    ref: str
    worker_id: str | None = None
    worker_route: InvocationRoute | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ProjectExecutionTarget":
        work_type = str(data.get("work_type", "")).strip()
        repository = str(data.get("repository", "")).strip()
        ref = str(data.get("ref", "")).strip()
        worker_id_raw = data.get("worker_id")
        worker_route_raw = data.get("worker_route")
        worker_id = (
            str(worker_id_raw).strip()
            if worker_id_raw is not None
            else None
        )
        worker_route = (
            InvocationRoute(str(worker_route_raw))
            if worker_route_raw is not None
            else None
        )
        if (worker_id is None) != (worker_route is None):
            raise ValueError(
                "execution target worker_id and worker_route must be declared together"
            )
        if worker_id == "":
            raise ValueError("execution target worker_id must not be empty")
        if not work_type:
            raise ValueError("execution target work_type is required")
        if _REPOSITORY_RE.fullmatch(repository) is None:
            raise ValueError("execution target repository must be owner/repo")
        if not ref:
            raise ValueError("execution target ref is required")
        return cls(
            work_type=work_type,
            repository=repository,
            ref=ref,
            worker_id=worker_id,
            worker_route=worker_route,
        )


class ProjectSchedulingState(str, Enum):
    SCHEDULABLE = "SCHEDULABLE"
    HELD = "HELD"
    ARCHIVED = "ARCHIVED"
    DORMANT = "DORMANT"
    SENSITIVE_HELD = "SENSITIVE_HELD"
    DECISION_HELD = "DECISION_HELD"

    @property
    def schedulable(self) -> bool:
        return self is ProjectSchedulingState.SCHEDULABLE


@dataclass(frozen=True)
class ProjectDefinition:
    id: str
    name: str
    visibility: str
    repositories: tuple[str, ...]
    capabilities: tuple[str, ...]
    assignment_scope: ProjectAssignmentScope
    review_scope: ProjectReviewScope
    scheduling_state: ProjectSchedulingState
    family_id: str
    execution_targets: tuple[ProjectExecutionTarget, ...] = ()
    scope_note: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ProjectDefinition":
        raw_repositories = data.get("repositories", [])
        raw_capabilities = data.get("capabilities", [])
        raw_execution_targets = data.get("execution_targets", [])
        if not isinstance(raw_repositories, list):
            raise ValueError("repositories must be a list")
        if not isinstance(raw_capabilities, list):
            raise ValueError("capabilities must be a list")
        if not isinstance(raw_execution_targets, list):
            raise ValueError("execution_targets must be a list")
        visibility = str(data["visibility"])
        if visibility not in {"public", "private"}:
            raise ValueError("visibility must be public or private")
        project_id = str(data["id"])
        family_id = str(data.get("family_id", project_id)).strip()
        if not family_id:
            raise ValueError("family_id must not be empty")
        scope_note = data.get("scope_note")
        repositories = tuple(str(repo) for repo in raw_repositories)
        execution_targets = tuple(
            ProjectExecutionTarget.from_mapping(item)
            for item in raw_execution_targets
        )
        target_work_types: set[str] = set()
        for target in execution_targets:
            if target.repository not in repositories:
                raise ValueError(
                    "execution target repository must belong to project repositories"
                )
            if target.work_type in target_work_types:
                raise ValueError(
                    "project execution target work_type must be unique"
                )
            target_work_types.add(target.work_type)
        return cls(
            id=project_id,
            name=str(data["name"]),
            visibility=visibility,
            repositories=repositories,
            capabilities=tuple(str(capability) for capability in raw_capabilities),
            assignment_scope=ProjectAssignmentScope(
                str(data.get("assignment_scope", ProjectAssignmentScope.NONE.value))
            ),
            review_scope=ProjectReviewScope(
                str(data.get("review_scope", ProjectReviewScope.NONE.value))
            ),
            scheduling_state=ProjectSchedulingState(
                str(
                    data.get(
                        "scheduling_state",
                        ProjectSchedulingState.SCHEDULABLE.value,
                    )
                )
            ),
            family_id=family_id,
            execution_targets=execution_targets,
            scope_note=str(scope_note) if scope_note is not None else None,
        )


class EvidenceClass(str, Enum):
    AUTHORITATIVE = "AUTHORITATIVE"
    DECLARED = "DECLARED"
    DERIVED = "DERIVED"
    CACHED = "CACHED"


@dataclass(frozen=True)
class ExactSubject:
    repository: str
    ref: str
    commit: str | None = None
    path: str | None = None
    digest: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ExactSubject":
        repository = str(data.get("repository", "")).strip()
        ref = str(data.get("ref", "")).strip()
        if not repository or not ref:
            raise ValueError("subject requires repository and ref")
        return cls(
            repository=repository,
            ref=ref,
            commit=str(data["commit"]) if data.get("commit") is not None else None,
            path=str(data["path"]) if data.get("path") is not None else None,
            digest=str(data["digest"]) if data.get("digest") is not None else None,
        )

    def identity(self) -> tuple[str, str, str | None, str | None, str | None]:
        return (self.repository, self.ref, self.commit, self.path, self.digest)


@dataclass(frozen=True)
class Observation:
    target: str
    evidence_class: EvidenceClass
    subject: ExactSubject
    observed_value: str
    observed_at: str
    observer: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Observation":
        raw_subject = data.get("subject")
        if not isinstance(raw_subject, Mapping):
            raise ValueError("observation subject must be a mapping")
        return cls(
            target=str(data["target"]),
            evidence_class=EvidenceClass(str(data["evidence_class"])),
            subject=ExactSubject.from_mapping(raw_subject),
            observed_value=str(data["observed_value"]),
            observed_at=str(data["observed_at"]),
            observer=str(data["observer"]),
        )


class DependencyReaction(str, Enum):
    NO_ACTION = "NO_ACTION"
    INSPECT = "INSPECT"
    RETEST = "RETEST"
    REREVIEW = "REREVIEW"
    REQUALIFY = "REQUALIFY"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class DependencySelector:
    repository: str
    ref: str | None = None
    path_prefix: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "DependencySelector":
        repository = str(data.get("repository", "")).strip()
        if not repository:
            raise ValueError("dependency selector requires repository")
        return cls(
            repository=repository,
            ref=str(data["ref"]) if data.get("ref") is not None else None,
            path_prefix=str(data["path_prefix"]) if data.get("path_prefix") is not None else None,
        )


@dataclass(frozen=True)
class DependencyEdge:
    id: str
    provider: str
    consumer: str
    kind: str
    selector: DependencySelector
    reaction: DependencyReaction
    evidence: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "DependencyEdge":
        raw_selector = data.get("selector")
        if not isinstance(raw_selector, Mapping):
            raise ValueError("dependency selector must be a mapping")
        return cls(
            id=str(data["id"]),
            provider=str(data["provider"]),
            consumer=str(data["consumer"]),
            kind=str(data["kind"]),
            selector=DependencySelector.from_mapping(raw_selector),
            reaction=DependencyReaction(str(data["reaction"])),
            evidence=str(data["evidence"]),
        )


class FrontierStatus(str, Enum):
    READY = "READY"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    WAITING_AUTHORITY = "WAITING_AUTHORITY"
    WAITING_SCHEDULING = "WAITING_SCHEDULING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_DETERMINISTIC = "FAILED_DETERMINISTIC"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    SUPERSEDED = "SUPERSEDED"


class CostClass(str, Enum):
    TRIVIAL = "TRIVIAL"
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


@dataclass(frozen=True)
class Frontier:
    id: str
    project: str
    subject: ExactSubject
    work_type: str
    reason: str
    dependencies: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    collision_keys: tuple[str, ...]
    cost_class: CostClass
    priority_inputs: Mapping[str, int]
    status: FrontierStatus

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "Frontier":
        raw_subject = data.get("subject")
        if not isinstance(raw_subject, Mapping):
            raise ValueError("frontier subject must be a mapping")
        raw_dependencies = data["dependencies"]
        raw_capabilities = data["required_capabilities"]
        raw_collision_keys = data["collision_keys"]
        raw_priority_inputs = data["priority_inputs"]
        if not isinstance(raw_dependencies, list):
            raise ValueError("dependencies must be a list")
        if not isinstance(raw_capabilities, list):
            raise ValueError("required_capabilities must be a list")
        if not isinstance(raw_collision_keys, list):
            raise ValueError("collision_keys must be a list")
        if not isinstance(raw_priority_inputs, Mapping):
            raise ValueError("priority_inputs must be a mapping")
        return cls(
            id=str(data["id"]),
            project=str(data["project"]),
            subject=ExactSubject.from_mapping(raw_subject),
            work_type=str(data["work_type"]),
            reason=str(data["reason"]),
            dependencies=tuple(str(item) for item in raw_dependencies),
            required_capabilities=tuple(str(item) for item in raw_capabilities),
            collision_keys=tuple(str(item) for item in raw_collision_keys),
            cost_class=CostClass(str(data["cost_class"])),
            priority_inputs={str(k): int(v) for k, v in raw_priority_inputs.items()},
            status=FrontierStatus(str(data["status"])),
        )
