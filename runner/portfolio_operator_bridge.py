from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Iterable, Mapping

from .budgets import BudgetEnvelope
from .durable_dispatch import SqliteDispatchAdmissionStore
from .github_backend import (
    GitHubBackend,
    GitHubOperation,
    GitHubRestTransport,
    GitHubTransport,
    TargetAuthorityGrant,
)
from .models import ExactSubject
from .portfolio_advancement import AdvancementItem, load_advancement_wave
from .portfolio_corpus import PortfolioRecord, load_portfolio_corpus
from .portfolio_operator_binding import bind_wave_to_operator_registry
from .portfolio_wave_scheduler import collision_keys
from .registry import load_project_snapshot
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INERT_ACTIONS = {"PRESERVE_ONLY", "REFRESH_IF_REACTIVATED"}
_CLAIMABLE_EFFECT_CEILINGS = {"NO_EFFECT", "SOURCE_ONLY"}


@dataclass(frozen=True)
class BoundPlanClaim:
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    plan_sha256: str
    wave_sha256: str
    work_fingerprint: str
    lineage_id: str
    holder: str
    fencing_token: int
    lease_expires_at: float
    budget_generation: int
    work_generation: int


@dataclass(frozen=True)
class VerifiedPlanSubject:
    item: AdvancementItem
    record: PortfolioRecord
    plan_sha256: str
    wave_sha256: str
    selected_payload: Mapping[str, object]
    operator_project_id: str
    operator_registry_sha256: str


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _git_blob_sha(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def _git_blob_sha_for_path(path: Path) -> str:
    raw = path.read_bytes()
    try:
        root = subprocess.check_output(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        relative = path.resolve().relative_to(Path(root).resolve()).as_posix()
        observed = subprocess.check_output(
            ["git", "-C", root, "hash-object", f"--path={relative}", str(path)],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        if _SHA40.fullmatch(observed):
            return observed
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    return _git_blob_sha(raw)


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _verify_plan_digest(payload: Mapping[str, object]) -> str:
    binding = _require_mapping(payload.get("plan_binding"), "plan binding")
    if binding.get("schema") != "PROJECT_RUNNER_PORTFOLIO_WAVE_PLAN_BINDING_V1":
        raise ValueError("unexpected plan binding schema")
    expected = str(binding.get("sha256", ""))
    if _SHA256.fullmatch(expected) is None:
        raise ValueError("plan binding sha256 is invalid")
    canonical_payload = dict(payload)
    canonical_payload.pop("plan_binding", None)
    observed = hashlib.sha256(_canonical_json_bytes(canonical_payload)).hexdigest()
    if not hmac.compare_digest(expected, observed):
        raise ValueError("admission plan digest mismatch")
    return observed


def _selected_item_payload(
    item: AdvancementItem,
) -> dict[str, object]:
    return {
        "subject_kind": item.subject_kind,
        "subject_id": item.subject_id,
        "family_id": item.family_id,
        "lead_identity": item.lead_identity,
        "reviewer_identities": list(item.reviewer_identities),
        "priority": item.priority,
        "action": item.action,
        "activity_state": item.activity_state,
        "effect_ceiling": item.effect_ceiling,
        "review_gate": item.review_gate,
        "frontier": item.frontier,
        "source_status": item.source_status,
        "collision_keys": list(collision_keys(item)),
    }


def verify_bound_plan_subject(
    *,
    plan_path: Path,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    subject_id: str,
) -> VerifiedPlanSubject:
    plan_raw = plan_path.read_bytes()
    try:
        plan_payload = json.loads(plan_raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("admission plan is not valid UTF-8 JSON") from exc
    if not isinstance(plan_payload, dict):
        raise ValueError("admission plan must be an object")
    if plan_payload.get("mode") != "PORTFOLIO_WAVE_ADMISSION_PLAN_V1":
        raise ValueError("unexpected admission plan mode")
    if plan_payload.get("execution_authority") is not False:
        raise ValueError("admission plan must not carry execution authority")
    if plan_payload.get("protected_effects_authorized") is not False:
        raise ValueError("admission plan must not authorize protected effects")

    plan_sha256 = _verify_plan_digest(plan_payload)

    wave_raw = wave_path.read_bytes()
    wave_sha256 = hashlib.sha256(wave_raw).hexdigest()
    wave_binding = _require_mapping(
        plan_payload.get("wave_binding"),
        "wave binding",
    )
    bound_wave_sha = str(wave_binding.get("sha256", ""))
    if not hmac.compare_digest(bound_wave_sha, wave_sha256):
        raise ValueError("admission plan wave digest mismatch")

    wave = load_advancement_wave(wave_path)
    if wave_binding.get("wave_id") != wave.wave_id:
        raise ValueError("admission plan wave id mismatch")
    if wave_binding.get("generated_at") != wave.generated_at:
        raise ValueError("admission plan wave timestamp mismatch")
    if wave_binding.get("corpus_binding") != dict(wave.corpus_binding):
        raise ValueError("admission plan corpus binding mismatch")

    binding_kind = wave.corpus_binding.get("binding_kind")
    if binding_kind == "LOCAL_SHA256":
        expected_sha256 = str(wave.corpus_binding.get("sha256", ""))
        if _SHA256.fullmatch(expected_sha256) is None:
            raise ValueError("wave local corpus sha256 binding is invalid")
        observed_sha256 = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        if not hmac.compare_digest(expected_sha256, observed_sha256):
            raise ValueError("local corpus sha256 does not match wave binding")
    elif binding_kind in (None, "GIT_BLOB"):
        expected_blob = str(wave.corpus_binding.get("git_blob_sha", ""))
        if not expected_blob:
            raise ValueError("wave corpus git blob binding is missing")
        if _git_blob_sha_for_path(corpus_path) != expected_blob:
            raise ValueError("local corpus does not match wave git blob binding")
    else:
        raise ValueError("unsupported wave corpus binding kind")
    corpus = load_portfolio_corpus(corpus_path, public_safe=True)

    selected_raw = plan_payload.get("selected")
    if not isinstance(selected_raw, list):
        raise ValueError("admission plan selected must be an array")
    selected_matches = [
        value
        for value in selected_raw
        if isinstance(value, Mapping)
        and str(value.get("subject_id", "")) == subject_id
    ]
    if len(selected_matches) != 1:
        raise ValueError("subject must appear exactly once in selected plan items")
    selected = selected_matches[0]

    wave_matches = [item for item in wave.items if item.subject_id == subject_id]
    if len(wave_matches) != 1:
        raise ValueError("subject must appear exactly once in source wave")
    item = wave_matches[0]
    if item.subject_kind != "repository":
        raise ValueError("bound plan bridge V1 supports repository subjects only")
    if item.execution_state != "QUEUED":
        raise ValueError("bound plan subject is not queued")
    if (
        item.action in _INERT_ACTIONS
        or item.effect_ceiling not in _CLAIMABLE_EFFECT_CEILINGS
    ):
        raise ValueError("bound plan subject is not claim-admissible")

    expected_selected = _selected_item_payload(item)
    if dict(selected) != expected_selected:
        raise ValueError("selected plan item does not match source wave")

    records = [record for record in corpus.records if record.id == subject_id]
    if len(records) != 1:
        raise ValueError("selected repository subject is missing from corpus")
    record = records[0]
    if record.repository != item.repositories[0]:
        raise ValueError("wave and corpus repository binding mismatch")
    if record.archived:
        raise ValueError("archived repository cannot be durably claimed")
    if not record.default_branch.strip():
        raise ValueError("corpus repository default branch is missing")

    registry = load_project_snapshot(projects_path)
    binding_report = bind_wave_to_operator_registry(
        wave,
        corpus,
        registry,
        public_safe=True,
    )
    binding_matches = [
        decision
        for decision in binding_report.decisions
        if decision.subject_id == subject_id
    ]
    if len(binding_matches) != 1:
        raise ValueError("subject must have exactly one operator binding decision")
    binding = binding_matches[0]
    if binding.state != "BOUND" or binding.operator_project_id is None:
        raise ValueError(
            f"subject is not operator-bound: {binding.reason}"
        )

    return VerifiedPlanSubject(
        item=item,
        record=record,
        plan_sha256=plan_sha256,
        wave_sha256=wave_sha256,
        selected_payload=dict(selected),
        operator_project_id=binding.operator_project_id,
        operator_registry_sha256=binding_report.operator_registry_sha256,
    )


def _read_live_head(
    *,
    repository: str,
    ref: str,
    token: str | None,
    transport: GitHubTransport | None,
) -> str:
    backend = GitHubBackend(
        transport=transport or GitHubRestTransport(token=token),
        route_capabilities=("github.read_ref",),
        grants=(
            TargetAuthorityGrant(
                repository=repository,
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=(ref,),
            ),
        ),
    )
    work = WorkUnit(
        id="portfolio-bound-plan-currentness-read",
        root_frontier_id="portfolio-bound-plan-currentness-read",
        parent_work_id=None,
        inputs=(),
        operation="GITHUB",
        required_capabilities=("github.read_ref",),
        collision_keys=(f"repository:{repository.lower()}",),
        recursion_depth=0,
        budget_allocation={"active": 1, "backend_jobs": 1},
        expected_outputs=("exact-head",),
        completion_criteria=("read exact current repository ref",),
        status=WorkUnitStatus.PENDING,
        payload={
            "github": {
                "operation": GitHubOperation.READ_REF.value,
                "repository": repository,
                "ref": ref,
            }
        },
    )
    result = backend.execute(work)
    if not result.succeeded or len(result.outputs) != 1:
        raise ValueError(
            f"live repository currentness read failed: {result.classification}"
        )
    head = result.outputs[0]
    if _SHA40.fullmatch(head) is None:
        raise ValueError("live repository currentness returned non-exact head")
    return head


def claim_bound_plan_subject(
    *,
    plan_path: Path,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    subject_id: str,
    state_db: Path,
    holder: str,
    lease_ttl: float,
    allowed_repositories: Iterable[str],
    token: str | None = None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> BoundPlanClaim:
    """Turn one bound plan selection into a durable CLAIMED record only.

    This function does not execute a backend, merge, deploy, install, mutate
    credentials/permissions, or assert runtime effect. It verifies the plan and
    wave/corpus bindings, re-reads the selected repository's exact current head,
    seeds immutable durable work, and acquires a fresh lease/fencing token.
    """
    if not subject_id.strip():
        raise ValueError("bound plan subject id is required")
    if not holder.strip():
        raise ValueError("bound plan lease holder is required")
    if lease_ttl <= 0:
        raise ValueError("bound plan lease ttl must be positive")

    verified = verify_bound_plan_subject(
        plan_path=Path(plan_path),
        wave_path=Path(wave_path),
        corpus_path=Path(corpus_path),
        projects_path=Path(projects_path),
        subject_id=subject_id,
    )
    repository = verified.record.repository
    authorized = frozenset(str(value) for value in allowed_repositories)
    if repository not in authorized:
        raise ValueError("selected repository is not authorized for durable claim")

    ref = verified.record.default_branch
    exact_head = _read_live_head(
        repository=repository,
        ref=ref,
        token=token,
        transport=transport,
    )
    exact_subject = ExactSubject(
        repository=repository,
        ref=ref,
        commit=exact_head,
    )

    lineage_id = (
        f"portfolio-plan:{verified.plan_sha256}:{subject_id}"
    )
    work = WorkUnit(
        id=f"portfolio-bound-claim:{subject_id}",
        root_frontier_id=f"portfolio-wave:{subject_id}",
        parent_work_id=None,
        inputs=(exact_subject,),
        operation="PORTFOLIO_BOUND_CLAIM",
        required_capabilities=("portfolio.claim",),
        collision_keys=collision_keys(verified.item),
        recursion_depth=0,
        budget_allocation={
            "children": 0,
            "active": 1,
            "retries": 0,
            "backend_jobs": 1,
        },
        expected_outputs=(),
        completion_criteria=(
            "fresh durable lease and fencing token acquired",
            "no backend execution performed by claim bridge",
        ),
        status=WorkUnitStatus.PENDING,
        payload={
            "schema": "PROJECT_RUNNER_BOUND_PLAN_CLAIM_V1",
            "subject_id": subject_id,
            "operator_project_id": verified.operator_project_id,
            "operator_registry_sha256": verified.operator_registry_sha256,
            "plan_sha256": verified.plan_sha256,
            "wave_sha256": verified.wave_sha256,
            "selected": dict(verified.selected_payload),
            "repository": repository,
            "ref": ref,
            "exact_head": exact_head,
            "execution_authority": False,
            "protected_effects_authorized": False,
        },
    )
    fingerprint = work_unit_fingerprint(work)
    budget = BudgetEnvelope(
        lineage_id=lineage_id,
        max_depth=0,
        depth=0,
        remaining_children=0,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
    )

    state_db = Path(state_db)
    state_db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteDispatchAdmissionStore(state_db)
    try:
        now = float(clock())
        try:
            budget_generation, work_generation = store.initialize_root(
                budget=budget,
                work=work,
                effective_capabilities=("portfolio.claim",),
            )
        except ValueError as exc:
            if str(exc) != "root execution state already exists":
                raise
            admitted = store.recover_claim_only_root(
                budget=budget,
                work=work,
                effective_capabilities=("portfolio.claim",),
                holder=holder,
                now=now,
                ttl=lease_ttl,
            )
        else:
            admitted = store.admit(
                lineage_id=lineage_id,
                work_fingerprint_value=fingerprint,
                budget_scope_id=budget.scope_id,
                expected_budget_generation=budget_generation,
                expected_work_generation=work_generation,
                holder=holder,
                now=now,
                ttl=lease_ttl,
            )
    finally:
        store.close()

    return BoundPlanClaim(
        subject_id=subject_id,
        repository=repository,
        ref=ref,
        exact_head=exact_head,
        plan_sha256=verified.plan_sha256,
        wave_sha256=verified.wave_sha256,
        work_fingerprint=fingerprint,
        lineage_id=lineage_id,
        holder=holder,
        fencing_token=admitted.lease.fencing_token,
        lease_expires_at=admitted.lease.expires_at,
        budget_generation=admitted.budget_generation,
        work_generation=admitted.work_generation,
    )