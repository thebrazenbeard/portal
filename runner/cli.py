from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Sequence

from .backends import MockBackend
from .budgets import BudgetEnvelope
from .collisions import partition_collision_groups
from .currentness import observation_locus, subject_changed
from .dedup import deduplicate_frontiers, frontier_fingerprint
from .dispatch import dispatch_ready
from .frontier import derive_frontiers
from .github_backend import GitHubBackend, GitHubOperation, GitHubRestTransport, TargetAuthorityGrant
from .execution_promotion import (
    execute_promoted,
    load_durable_promotion_receipt,
    effect_authority_key_from_environment,
    execution_authority_key_from_environment,
    load_json_document,
    promote_claimed_to_running,
    review_key_from_environment,
)
from .leases import InMemoryLeaseStore
from .models import (
    ExactSubject,
    FrontierStatus,
    InvocationRoute,
    ProjectSchedulingState,
)
from .operator import (
    build_github_read_backend,
    run_durable_github_read_inspection,
    summarize_recovery_state,
)
from .portfolio import collect_and_schedule_portfolio, summarize_portfolio_state
from .portfolio_advancement import load_advancement_wave
from .portfolio_corpus import load_portfolio_corpus
from .portfolio_operator_binding import bind_wave_to_operator_registry
from .portfolio_operator_bridge import claim_bound_plan_subject
from .portfolio_wave_scheduler import WaveExecutionBudget, plan_wave_admission
from .promoted_github import PromotedGitHubSourceWriteBackend
from .promoted_github_runtime import (
    finalize_github_source_write_effect_confirmed,
    qualify_github_source_write_runtime,
    reconcile_github_source_write_outcome_unknown,
)
from .queue_consumer import (
    consume_next_queued_read_only_work,
    reconcile_queue_item,
    summarize_queue_state,
)
from .reference_worker import run_reference_read_worker_once
from .task_monitor import (
    default_tasks_root,
    finalize_task,
    reconcile_orphaned_tasks,
    register_task,
    summarize_task_history,
    summarize_tasks,
)
from .task_supervisor import launch_background_task
from .prioritize import rank_frontiers
from .propagate import derive_invalidations
from .registry import (
    load_dependencies,
    load_dependency_snapshot,
    load_observations,
    load_project_snapshot,
    load_worker_snapshot,
    load_workers,
)
from .worker_routing import SqliteWorkerRouteStore, summarize_worker_routes
from .verify import verify_attempt
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


ROOT = Path(__file__).resolve().parents[1]


def _project_registry_path() -> Path:
    override = os.environ.get("PROJECT_RUNNER_PROJECT_REGISTRY")
    if override is None:
        return ROOT / "registry" / "projects.yaml"

    path = Path(override).expanduser()
    if not path.is_absolute():
        raise ValueError("external project registry requires an absolute path")

    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError("external project registry is unavailable") from None

    root = ROOT.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError(
            "external project registry must be outside the Project Runner checkout"
        )
    if not resolved.is_file():
        raise ValueError("external project registry is unavailable")
    return resolved


def _external_project_registry_selected() -> bool:
    return os.environ.get("PROJECT_RUNNER_PROJECT_REGISTRY") is not None


def _external_project_registry_expected_sha256() -> str:
    expected = os.environ.get("PROJECT_RUNNER_PROJECT_REGISTRY_SHA256", "")
    if (
        len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise ValueError(
            "external project registry requires an expected lowercase SHA-256"
        )
    return expected


def _external_private_collision_key() -> str:
    key = os.environ.get("PROJECT_RUNNER_PRIVATE_COLLISION_KEY", "")
    if (
        len(key) != 64
        or any(character not in "0123456789abcdef" for character in key)
    ):
        raise ValueError(
            "external project registry requires a private collision key"
        )
    return key


def _load_project_registry_snapshot():
    external = _external_project_registry_selected()
    path = _project_registry_path()
    expected = (
        _external_project_registry_expected_sha256()
        if external
        else None
    )
    snapshot = load_project_snapshot(
        path,
        require_scope_metadata=external,
    )
    if expected is not None and not hmac.compare_digest(snapshot.sha256, expected):
        raise ValueError("external project registry digest mismatch")
    return snapshot


def _load_project_registry():
    return _load_project_registry_snapshot().projects


def _require_public_safe_reporting() -> None:
    if _external_project_registry_selected():
        raise ValueError(
            "detailed reports are disabled with an external project registry"
        )


def _load_worker_registry_snapshot():
    return load_worker_snapshot(ROOT / "registry" / "workers.yaml")


def _load_all():
    projects = _load_project_registry()
    workers = _load_worker_registry_snapshot().workers
    return projects, workers


def _validate() -> int:
    projects, workers = _load_all()
    print(f"Registries valid: {len(projects)} projects, {len(workers)} workers")
    return 0


def _inventory() -> int:
    projects, workers = _load_all()
    counts = Counter(worker.lifecycle.value for worker in workers)
    print(f"Projects: {len(projects)}")
    print(f"Workers: {len(workers)}")
    for lifecycle in sorted(counts):
        print(f"{lifecycle.title()}: {counts[lifecycle]}")
    return 0


def _subject_payload(subject) -> dict[str, str | None]:
    return {
        "repository": subject.repository,
        "ref": subject.ref,
        "commit": subject.commit,
        "path": subject.path,
        "digest": subject.digest,
    }


def _evaluate_change(before: Path, after: Path, dependencies: Path) -> int:
    _require_public_safe_reporting()
    previous = load_observations(before)
    current = load_observations(after)
    edges = load_dependencies(dependencies)

    previous_by_locus = {observation_locus(item): item for item in previous}
    changed = [
        item for item in current
        if subject_changed(previous_by_locus.get(observation_locus(item)), item)
    ]
    invalidations = derive_invalidations(previous, current, edges)

    payload = {
        "changed_subjects": [
            {"target": item.target, **_subject_payload(item.subject)}
            for item in sorted(
                changed,
                key=lambda item: (
                    item.target,
                    item.subject.repository,
                    item.subject.ref,
                    item.subject.path or "",
                    item.subject.commit or "",
                ),
            )
        ],
        "invalidations": [
            {
                "dependency_id": item.dependency_id,
                "provider": item.provider,
                "consumer": item.consumer,
                "reaction": item.reaction.value,
                "subject": _subject_payload(item.changed_subject),
            }
            for item in sorted(
                invalidations,
                key=lambda item: (
                    item.dependency_id,
                    item.consumer,
                    item.changed_subject.repository,
                    item.changed_subject.path or "",
                ),
            )
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload["plan_binding"] = {
        "schema": "PROJECT_RUNNER_PORTFOLIO_WAVE_PLAN_BINDING_V1",
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _frontier_payload(frontier) -> dict[str, object]:
    return {
        "id": frontier.id,
        "fingerprint": frontier_fingerprint(frontier),
        "project": frontier.project,
        "subject": _subject_payload(frontier.subject),
        "work_type": frontier.work_type,
        "reason": frontier.reason,
        "dependencies": list(frontier.dependencies),
        "required_capabilities": list(frontier.required_capabilities),
        "collision_keys": list(frontier.collision_keys),
        "cost_class": frontier.cost_class.value,
        "priority_inputs": dict(sorted(frontier.priority_inputs.items())),
        "status": frontier.status.value,
    }


def _private_collision_key(key: str, private_collision_key: str) -> str:
    digest = hmac.new(
        bytes.fromhex(private_collision_key),
        key.encode("utf-8"),
        digestmod="sha256",
    ).hexdigest()
    return f"private:{digest}"


def _derive_frontier_set(
    before: Path,
    after: Path,
    dependencies: Path,
    *,
    project_snapshot=None,
):
    previous = load_observations(before)
    current = load_observations(after)
    edges = load_dependencies(dependencies)
    snapshot = project_snapshot or _load_project_registry_snapshot()
    projects = snapshot.projects
    capability_lookup = {
        project.id: set(project.capabilities)
        for project in projects
    }
    scheduling_lookup = {
        project.id: project.scheduling_state.schedulable
        for project in projects
    }

    invalidations = derive_invalidations(previous, current, edges)
    derived = derive_frontiers(
        invalidations,
        capability_lookup=capability_lookup,
        scheduling_lookup=scheduling_lookup,
    )
    if _external_project_registry_selected():
        private_collision_key = _external_private_collision_key()
        derived = tuple(
            replace(
                frontier,
                collision_keys=tuple(
                    _private_collision_key(key, private_collision_key)
                    for key in frontier.collision_keys
                ),
            )
            for frontier in derived
        )
    return deduplicate_frontiers(derived)


def _frontier_summary(before: Path, after: Path, dependencies: Path) -> int:
    try:
        frontiers = _derive_frontier_set(before, after, dependencies)
    except Exception:
        if _external_project_registry_selected():
            raise ValueError(
                "external frontier summary is unavailable or structurally invalid"
            ) from None
        raise

    status_counts = Counter(frontier.status.value for frontier in frontiers)
    ready = status_counts.get(FrontierStatus.READY.value, 0)
    payload = {
        "total": len(frontiers),
        "ready": ready,
        "blocked": len(frontiers) - ready,
        "statuses": dict(sorted(status_counts.items())),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _frontier_report(before: Path, after: Path, dependencies: Path) -> int:
    _require_public_safe_reporting()
    frontiers = _derive_frontier_set(before, after, dependencies)
    ordered_frontiers = tuple(sorted(frontiers, key=frontier_fingerprint))
    collision_groups = partition_collision_groups(ordered_frontiers)
    ranked = rank_frontiers(ordered_frontiers)

    payload = {
        "frontiers": [_frontier_payload(item) for item in ordered_frontiers],
        "collision_groups": [
            [item.id for item in group]
            for group in collision_groups
        ],
        "ranked": [
            {
                "id": decision.frontier.id,
                "project": decision.frontier.project,
                "work_type": decision.frontier.work_type,
                "status": decision.frontier.status.value,
                "score": decision.score,
                "reasons": list(decision.reasons),
            }
            for decision in ranked
        ],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _dispatch_report(before: Path, after: Path, dependencies: Path) -> int:
    _require_public_safe_reporting()
    frontiers = _derive_frontier_set(before, after, dependencies)
    blocked = tuple(
        sorted(
            (
                frontier
                for frontier in frontiers
                if frontier.status is not FrontierStatus.READY
            ),
            key=frontier_fingerprint,
        )
    )

    budget = BudgetEnvelope(
        lineage_id="m4-cli-fixture",
        max_depth=3,
        depth=0,
        remaining_children=16,
        remaining_active=8,
        remaining_retries=4,
        remaining_backend_jobs=8,
    )
    store = InMemoryLeaseStore()
    backend = MockBackend()
    batch = dispatch_ready(
        frontiers,
        lease_store=store,
        backend=backend,
        budget=budget,
        holder="project-runner-m4-mock",
        now=0.0,
        lease_ttl=60.0,
    )

    attempts = []
    for attempt in batch.attempts:
        outcome = verify_attempt(
            attempt,
            lease_store=store,
            now=1.0,
            current_subject_reader=lambda subject: subject,
            evidence_verifier=lambda work, result: (
                result.succeeded and "mock-backend" in result.evidence
            ),
        )
        attempts.append(
            {
                "frontier_id": attempt.frontier.id,
                "project": attempt.frontier.project,
                "work_id": attempt.work.id,
                "work_fingerprint": work_unit_fingerprint(attempt.work),
                "fencing_token": attempt.lease.fencing_token,
                "backend_succeeded": attempt.result.succeeded,
                "verification_status": outcome.status.value,
                "verification_reason": outcome.reason,
            }
        )

    payload = {
        "mode": "M4_MOCK_NO_DOWNSTREAM_EFFECTS",
        "attempts": attempts,
        "blocked": [
            {
                "id": frontier.id,
                "project": frontier.project,
                "work_type": frontier.work_type,
                "status": frontier.status.value,
            }
            for frontier in blocked
        ],
        "remaining_budget": {
            "children": batch.remaining_budget.remaining_children,
            "active": batch.remaining_budget.remaining_active,
            "retries": batch.remaining_budget.remaining_retries,
            "backend_jobs": batch.remaining_budget.remaining_backend_jobs,
        },
    }
    print(json.dumps(payload, sort_keys=True))
    return 0




def _portfolio_wave_plan(
    wave_path: Path,
    *,
    max_parallel: int,
    max_per_identity: int,
    max_per_family: int,
    max_per_lane: int | None,
    occupied_collision_keys: Sequence[str],
) -> int:
    wave_bytes = wave_path.read_bytes()
    wave_sha256 = hashlib.sha256(wave_bytes).hexdigest()
    wave = load_advancement_wave(wave_path)
    plan = plan_wave_admission(
        wave,
        budget=WaveExecutionBudget(
            max_parallel=max_parallel,
            max_per_identity=max_per_identity,
            max_per_family=max_per_family,
            max_per_lane=max_per_lane,
        ),
        occupied_collision_keys=occupied_collision_keys,
    )
    payload = {
        "mode": "PORTFOLIO_WAVE_ADMISSION_PLAN_V1",
        "execution_authority": False,
        "protected_effects_authorized": False,
        "wave_binding": {
            "sha256": wave_sha256,
            "wave_id": wave.wave_id,
            "generated_at": wave.generated_at,
            "corpus_binding": dict(wave.corpus_binding),
        },
        "summary": plan.summary(),
        "lane_assignments": {
            f"{item.subject_kind}:{item.subject_id}": item.effective_lane
            for item in wave.items
        },
        "selected": [
            {
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
                "collision_keys": list(item.collision_keys),
            }
            for item in plan.selected
        ],
        "deferred": [
            {
                "subject_kind": item.subject_kind,
                "subject_id": item.subject_id,
                "family_id": item.family_id,
                "lead_identity": item.lead_identity,
                "priority": item.priority,
                "reason": item.reason,
                "collision_keys": list(item.collision_keys),
            }
            for item in plan.deferred
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload["plan_binding"] = {
        "schema": "PROJECT_RUNNER_PORTFOLIO_WAVE_PLAN_BINDING_V1",
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _portfolio_operator_bindings(
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
) -> int:
    wave = load_advancement_wave(wave_path)
    corpus = load_portfolio_corpus(corpus_path, public_safe=True)
    registry = load_project_snapshot(projects_path)
    report = bind_wave_to_operator_registry(
        wave,
        corpus,
        registry,
        public_safe=True,
    )
    payload = {
        "mode": "PORTFOLIO_OPERATOR_BINDING_REPORT_V1",
        "execution_authority": False,
        "summary": report.summary(),
        "decisions": [
            {
                "subject_kind": item.subject_kind,
                "subject_id": item.subject_id,
                "repository": item.repository,
                "state": item.state,
                "reason": item.reason,
                "operator_project_id": item.operator_project_id,
            }
            for item in report.decisions
        ],
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _portfolio_wave_claim(
    *,
    plan_path: Path,
    wave_path: Path,
    corpus_path: Path,
    projects_path: Path,
    subject_id: str,
    state_db: Path,
    holder: str,
    lease_ttl: float,
    allowed_repositories: Sequence[str],
) -> int:
    claim = claim_bound_plan_subject(
        plan_path=plan_path,
        wave_path=wave_path,
        corpus_path=corpus_path,
        projects_path=projects_path,
        subject_id=subject_id,
        state_db=state_db,
        holder=holder,
        lease_ttl=lease_ttl,
        allowed_repositories=allowed_repositories,
        token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
    )
    payload = {
        "mode": "PORTFOLIO_BOUND_PLAN_CLAIM_V1",
        "execution_authority": False,
        "protected_effects_authorized": False,
        "backend_execution_performed": False,
        "subject_id": claim.subject_id,
        "repository": claim.repository,
        "ref": claim.ref,
        "exact_head": claim.exact_head,
        "plan_sha256": claim.plan_sha256,
        "wave_sha256": claim.wave_sha256,
        "work_fingerprint": claim.work_fingerprint,
        "lineage_id": claim.lineage_id,
        "holder": claim.holder,
        "fencing_token": claim.fencing_token,
        "lease_expires_at": claim.lease_expires_at,
        "budget_generation": claim.budget_generation,
        "work_generation": claim.work_generation,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _portfolio_wave_promote(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    holder: str,
    review_path: Path,
    execution_grant_path: Path,
    effect_grant_path: Path | None,
) -> int:
    review_document = load_json_document(review_path)
    execution_grant_document = load_json_document(execution_grant_path)
    effect_grant_document = (
        load_json_document(effect_grant_path)
        if effect_grant_path is not None
        else None
    )
    receipt = promote_claimed_to_running(
        state_db=state_db,
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
        holder=holder,
        review_document=review_document,
        execution_grant_document=execution_grant_document,
        effect_grant_document=effect_grant_document,
        review_key=review_key_from_environment(),
        execution_authority_key=execution_authority_key_from_environment(),
        effect_authority_key=effect_authority_key_from_environment(),
        token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
    )
    payload = {
        "mode": "PORTFOLIO_EXECUTION_PROMOTION_V1",
        "backend_execution_performed": False,
        "lineage_id": receipt.lineage_id,
        "work_fingerprint": receipt.work_fingerprint,
        "fencing_token": receipt.fencing_token,
        "holder": receipt.holder,
        "repository": receipt.repository,
        "ref": receipt.ref,
        "exact_head": receipt.exact_head,
        "operation": receipt.operation,
        "effect_class": receipt.effect_class,
        "review_sha256": receipt.review_sha256,
        "review_valid_until": receipt.review_valid_until,
        "execution_grant_sha256": receipt.execution_grant_sha256,
        "execution_valid_until": receipt.execution_valid_until,
        "execution_request_sha256": receipt.execution_request_sha256,
        "effect_grant_sha256": receipt.effect_grant_sha256,
        "effect_valid_until": receipt.effect_valid_until,
        "promoted_at": receipt.promoted_at,
        "attempt_work_generation": receipt.attempt_work_generation,
        "promoted_work_generation": receipt.promoted_work_generation,
        "promotion_sha256": receipt.promotion_sha256,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _github_source_write_execute(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
) -> int:
    """Execute an already-promoted exact source write; never mint authority."""
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    if not token:
        raise ValueError("PROJECT_RUNNER_GITHUB_TOKEN is required for source writes")
    receipt = load_durable_promotion_receipt(
        state_db=state_db,
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
    )
    transport = GitHubRestTransport(token=token)
    result = execute_promoted(
        state_db=state_db,
        receipt=receipt,
        backend=PromotedGitHubSourceWriteBackend(transport=transport),
        transport=transport,
    )
    print(json.dumps({
        "mode": "GITHUB_SOURCE_WRITE_EXECUTION_V1",
        "lineage_id": lineage_id,
        "work_fingerprint": result.work_fingerprint,
        "fencing_token": fencing_token,
        "status": result.classification,
        "succeeded": result.succeeded,
        "outputs": list(result.outputs),
        "evidence": list(result.evidence),
        "backend_executed": True,
        "deployment_effect_claimed": False,
    }, sort_keys=True))
    return 0 if result.succeeded else (2 if result.classification == "OUTCOME_UNKNOWN" else 1)


def _github_source_write_runtime_qualify(
    repository: str,
    ref: str,
) -> int:
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "PROJECT_RUNNER_GITHUB_TOKEN is required for runtime qualification"
        )
    result = qualify_github_source_write_runtime(
        repository=repository,
        ref=ref,
        transport=GitHubRestTransport(token=token),
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if result.status == "PASS" else 1


def _github_source_write_finalize_effect(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
) -> int:
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "PROJECT_RUNNER_GITHUB_TOKEN is required for effect finalization"
        )
    result = finalize_github_source_write_effect_confirmed(
        state_db=state_db,
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
        transport=GitHubRestTransport(token=token),
    )
    print(json.dumps({
        "mode": "GITHUB_SOURCE_WRITE_EFFECT_FINALIZATION_V1",
        "status": result.status,
        "reason": result.reason,
        "candidate_commit_sha": result.candidate_commit_sha,
        "candidate_blob_sha": result.candidate_blob_sha,
        "reconciliation_sha256": result.reconciliation_sha256,
        "work_generation": result.work_generation,
        "verified_at": result.verified_at,
        "backend_replayed": result.backend_replayed,
        "finalization_replayed": result.finalization_replayed,
        "deployment_effect_claimed": False,
        "installation_effect_claimed": False,
    }, sort_keys=True))
    return 0


def _github_source_write_reconcile(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    reconciler: str,
) -> int:
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "PROJECT_RUNNER_GITHUB_TOKEN is required for source-write reconciliation"
        )
    result = reconcile_github_source_write_outcome_unknown(
        state_db=state_db,
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
        transport=GitHubRestTransport(token=token),
        reconciler=reconciler,
    )
    print(json.dumps({
        "mode": "GITHUB_SOURCE_WRITE_OUTCOME_RECONCILIATION_V1",
        "backend_replayed": False,
        "outcome": result.outcome,
        "reason": result.reason,
        "evidence": list(result.evidence),
        "observed_head": result.observed_head,
        "observed_blob_sha": result.observed_blob_sha,
        "work_generation": result.work_generation,
    }, sort_keys=True))
    return 0 if result.outcome != "INDETERMINATE" else 2


def _github_read_smoke(repository: str, ref: str, expected_head: str | None) -> int:
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    backend = GitHubBackend(
        transport=GitHubRestTransport(token=token),
        route_capabilities={"github.read_ref"},
        grants=(
            TargetAuthorityGrant(
                repository=repository,
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=(ref,),
            ),
        ),
    )
    work = WorkUnit(
        id="github-read-smoke",
        root_frontier_id="github-read-smoke",
        parent_work_id=None,
        inputs=(),
        operation="GITHUB",
        required_capabilities=("read",),
        collision_keys=(f"repo:{repository}",),
        recursion_depth=0,
        budget_allocation={"active": 1, "backend_jobs": 1},
        expected_outputs=("github-ref",),
        completion_criteria=("readback",),
        status=WorkUnitStatus.PENDING,
        payload={
            "github": {
                "operation": "READ_REF",
                "repository": repository,
                "ref": ref,
                "expected_head": expected_head,
            }
        },
    )
    result = backend.execute(work)
    print(json.dumps({
        "classification": result.classification,
        "succeeded": result.succeeded,
        "outputs": list(result.outputs),
        "evidence": list(result.evidence),
    }, sort_keys=True))
    return 0 if result.succeeded else 1


def _select_ready_inspection(frontiers, *, project: str, frontier_id: str | None):
    candidates = tuple(
        frontier
        for frontier in frontiers
        if frontier.status is FrontierStatus.READY
        and frontier.work_type == "INSPECT"
        and frontier.project == project
        and (frontier_id is None or frontier.id == frontier_id)
    )
    if not candidates:
        raise ValueError("no matching READY INSPECT frontier")
    if len(candidates) != 1:
        raise ValueError(
            "multiple matching READY INSPECT frontiers; supply --frontier-id"
        )
    return candidates[0]


def _run_inspection(args) -> int:
    registry_snapshot = _load_project_registry_snapshot()
    frontiers = _derive_frontier_set(
        args.before,
        args.after,
        args.dependencies,
        project_snapshot=registry_snapshot,
    )
    frontier = _select_ready_inspection(
        frontiers,
        project=args.project,
        frontier_id=args.frontier_id,
    )
    target = ExactSubject(
        repository=args.target_repository,
        ref=args.target_ref,
        commit=args.target_head,
    )
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    project = next(
        (
            item
            for item in registry_snapshot.projects
            if item.id == frontier.project
        ),
        None,
    )
    if project is None:
        raise ValueError("frontier project is absent from the current registry")
    lineage_id = args.lineage_id or (
        "inspect:"
        + frontier_fingerprint(frontier)[:24]
        + ":"
        + args.target_head[:12]
    )
    result = run_durable_github_read_inspection(
        frontier=frontier,
        target_subject=target,
        state_db=args.state_db,
        lineage_id=lineage_id,
        holder=args.holder,
        lease_ttl=args.lease_ttl,
        registry_digest=registry_snapshot.sha256,
        authorized_target_repositories=project.repositories,
        authorized_provider_repositories=tuple(
            repository
            for item in registry_snapshot.projects
            for repository in item.repositories
        ),
        token=token,
    )
    payload = {
        "mode": "M6_DURABLE_GITHUB_READ_INSPECTION",
        "status": result.status.value,
        "reason": result.reason,
        "backend_classification": result.backend_classification,
        "lineage_id": result.lineage_id,
        "work_fingerprint": result.work_fingerprint,
        "fencing_token": result.fencing_token,
        "budget_generation": result.budget_generation,
        "work_generation": result.work_generation,
    }
    if not _external_project_registry_selected():
        payload["project"] = frontier.project
        payload["frontier_id"] = frontier.id
    print(json.dumps(payload, sort_keys=True))
    return 0 if result.status is WorkUnitStatus.COMPLETE else 2


def _task_start(args) -> int:
    launch = launch_background_task(
        name=args.name,
        command=args.task_command,
        owner=args.owner,
        repository=args.repository,
        worktree=args.worktree,
        lane=args.lane,
        work_unit=args.work_unit,
        display_command=args.display_command,
        shell=args.shell,
        working_directory=args.working_directory,
        tasks_root=args.tasks_root,
        registration_timeout_seconds=args.registration_timeout,
    )
    print(json.dumps(launch, sort_keys=True))
    return 0


def _task_register(args) -> int:
    task = register_task(
        args.tasks_root,
        name=args.name,
        pid=args.pid,
        owner=args.owner,
        repository=args.repository,
        worktree=args.worktree,
        lane=args.lane,
        work_unit=args.work_unit,
        command=args.display_command,
        shell=args.shell,
        working_directory=args.working_directory,
        process_started_at_utc=args.process_started_at_utc,
        stdout_log=args.stdout_log,
        stderr_log=args.stderr_log,
    )
    print(json.dumps({"mode": "PROJECT_RUNNER_TASK_REGISTER_V1", "task": task}, sort_keys=True))
    return 0


def _task_status(args) -> int:
    print(json.dumps(
        summarize_tasks(args.tasks_root, lane=args.lane),
        sort_keys=True,
    ))
    return 0


def _task_finalize(args) -> int:
    task = finalize_task(
        args.tasks_root,
        task_id=args.task_id,
        exit_code=args.exit_code,
        terminal_reason=args.terminal_reason,
    )
    print(json.dumps({"mode": "PROJECT_RUNNER_TASK_FINALIZE_V1", "task": task}, sort_keys=True))
    return 0


def _task_history(args) -> int:
    print(json.dumps(
        summarize_task_history(args.tasks_root, lane=args.lane),
        sort_keys=True,
    ))
    return 0


def _task_reconcile(args) -> int:
    tasks = reconcile_orphaned_tasks(args.tasks_root)
    print(json.dumps({
        "mode": "PROJECT_RUNNER_TASK_RECONCILE_V1",
        "reconciled": len(tasks),
        "tasks": tasks,
    }, sort_keys=True))
    return 0


def _operator_status(args) -> int:
    if args.detailed:
        _require_public_safe_reporting()
    payload = summarize_recovery_state(
        args.state_db,
        now=time.time(),
        detailed=args.detailed,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0

def _portfolio_cycle(args) -> int:
    external = _external_project_registry_selected()
    try:
        registry_snapshot = _load_project_registry_snapshot()
        dependency_snapshot = load_dependency_snapshot(args.dependencies)
        worker_snapshot = _load_worker_registry_snapshot()
        collision_key = (
            _external_private_collision_key()
            if external
            else None
        )
        result = collect_and_schedule_portfolio(
            projects=registry_snapshot.projects,
            dependencies=dependency_snapshot.dependencies,
            workers=worker_snapshot.workers,
            registry_digest=registry_snapshot.sha256,
            dependency_digest=dependency_snapshot.sha256,
            worker_registry_digest=worker_snapshot.sha256,
            state_db=args.state_db,
            token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
            private_collision_key=collision_key,
        )
    except Exception:
        if external:
            raise ValueError(
                "external portfolio cycle is unavailable or structurally invalid"
            ) from None
        raise

    print(
        json.dumps(
            {
                "mode": "M6_DURABLE_PORTFOLIO_CURRENTNESS",
                "snapshot_id": result.snapshot_id,
                "snapshot_digest": result.snapshot_digest,
                "baseline": result.baseline,
                "observations": result.observation_count,
                "changed": result.changed_count,
                "frontiers": result.frontier_count,
                "ready": result.ready_count,
                "blocked": result.blocked_count,
                "queued": result.queued_count,
            },
            sort_keys=True,
        )
    )
    return 0


def _portfolio_status(args) -> int:
    print(json.dumps(summarize_portfolio_state(args.state_db), sort_keys=True))
    return 0


def _consume_queue(args) -> int:
    external = _external_project_registry_selected()
    try:
        registry_snapshot = _load_project_registry_snapshot()
        dependency_snapshot = load_dependency_snapshot(args.dependencies)
        worker_snapshot = _load_worker_registry_snapshot()
        result = consume_next_queued_read_only_work(
            projects=registry_snapshot.projects,
            workers=worker_snapshot.workers,
            registry_digest=registry_snapshot.sha256,
            dependency_digest=dependency_snapshot.sha256,
            worker_registry_digest=worker_snapshot.sha256,
            state_db=args.state_db,
            holder=args.holder,
            lease_ttl=args.lease_ttl,
            token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
        )
    except Exception:
        if external:
            raise ValueError(
                "external queue consumption is unavailable or structurally invalid"
            ) from None
        raise

    payload = {
        "mode": "M6_FENCED_READ_ONLY_QUEUE_CONSUMPTION",
        "claimed": result.claimed,
        "queue_state": result.queue_state,
        "snapshot_id": result.snapshot_id,
        "fencing_token": result.fencing_token,
        "operator_status": result.operator_status,
        "route_id": result.route_id,
        "reason": result.reason,
    }
    if not external:
        payload["frontier_fingerprint"] = result.frontier_fingerprint
    print(json.dumps(payload, sort_keys=True))
    return 0 if result.queue_state in {
        "NO_WORK", "COMPLETE", "SUPERSEDED", "ROUTED"
    } else 2


def _queue_status(args) -> int:
    print(json.dumps(summarize_queue_state(args.state_db), sort_keys=True))
    return 0


def _worker_route_status(args) -> int:
    print(json.dumps(summarize_worker_routes(args.state_db), sort_keys=True))
    return 0


def _secure_windows_worker_packet(path: Path) -> None:
    """Remove broad inherited NTFS permissions before writing private data."""
    identity = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    try:
        sid = next(csv.reader([identity]))[1]
    except (IndexError, StopIteration) as exc:
        raise ValueError("cannot resolve Windows worker-packet owner") from exc
    if re.fullmatch(r"S-\d+(?:-\d+)+", sid) is None:
        raise ValueError("invalid Windows worker-packet owner SID")
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
        check=True, text=True, capture_output=True,
    )


def _claim_worker_route(args) -> int:
    payload_out = args.payload_out.expanduser().resolve()
    root = ROOT.resolve()
    payload_inside_public_checkout = (
        payload_out == root or root in payload_out.parents
    )
    if _external_project_registry_selected() and payload_inside_public_checkout:
        raise ValueError(
            "private worker payload must be written outside the public checkout"
        )
    payload_out.parent.mkdir(parents=True, exist_ok=True)

    worker_snapshot = _load_worker_registry_snapshot()
    route = InvocationRoute(args.route)
    store = SqliteWorkerRouteStore(args.state_db)
    try:
        claim = store.claim_next(
            worker_id=args.worker_id,
            invocation_route=route,
            workers=worker_snapshot.workers,
            holder=args.holder,
            now=time.time(),
            ttl=args.lease_ttl,
            worker_registry_digest=worker_snapshot.sha256,
            allow_private=not payload_inside_public_checkout,
        )
    finally:
        store.close()

    if claim is None:
        print(json.dumps({
            "mode": "M6_WORKER_ROUTE_PULL",
            "claimed": False,
            "state": "NO_WORK",
        }, sort_keys=True))
        return 0

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{payload_out.name}.", suffix=".tmp", dir=payload_out.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if os.name == "nt":
                _secure_windows_worker_packet(temporary)
            handle.write(json.dumps(claim.payload, sort_keys=True) + "\n")
        os.replace(temporary, payload_out)
        if os.name != "nt":
            os.chmod(payload_out, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    print(json.dumps({
        "mode": "M6_WORKER_ROUTE_PULL",
        "claimed": True,
        "route_id": claim.route_id,
        "worker_id": claim.worker_id,
        "invocation_route": claim.invocation_route.value,
        "delivery_fencing_token": claim.fencing_token,
        "expires_at": claim.expires_at,
    }, sort_keys=True))
    return 0


def _run_reference_worker(args) -> int:
    worker_snapshot = _load_worker_registry_snapshot()
    result = run_reference_read_worker_once(
        state_db=args.state_db,
        workers=worker_snapshot.workers,
        worker_registry_digest=worker_snapshot.sha256,
        holder=args.holder,
        lease_ttl=args.lease_ttl,
        token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
    )
    print(json.dumps({
        "mode": "M6_REFERENCE_READ_WORKER",
        "claimed": result.claimed,
        "route_id": result.route_id,
        "delivery_fencing_token": result.delivery_fencing_token,
        "receipt_class": result.receipt_class,
        "receipt_sha256": result.receipt_sha256,
        "reason": result.reason,
    }, sort_keys=True))
    if not result.claimed:
        return 0
    return 0 if result.receipt_class in {"SUCCEEDED", "SUPERSEDED"} else 2


def _record_worker_receipt(args) -> int:
    route = InvocationRoute(args.route)
    store = SqliteWorkerRouteStore(args.state_db)
    try:
        receipt = store.record_receipt(
            route_id=args.route_id,
            worker_id=args.worker_id,
            invocation_route=route,
            holder=args.holder,
            expected_fencing_token=args.expected_fencing_token,
            receipt_class=args.receipt_class,
            receipt_sha256=args.receipt_sha256,
            now=time.time(),
        )
    finally:
        store.close()
    print(json.dumps({
        "mode": "M6_WORKER_ROUTE_RECEIPT",
        "route_id": receipt.route_id,
        "worker_id": receipt.worker_id,
        "invocation_route": receipt.invocation_route.value,
        "delivery_fencing_token": receipt.fencing_token,
        "receipt_class": receipt.receipt_class,
        "receipt_sha256": receipt.receipt_sha256,
        "state": receipt.state,
    }, sort_keys=True))
    return 0


def _reconcile_queue(args) -> int:
    external = _external_project_registry_selected()
    try:
        result = reconcile_queue_item(
            state_db=args.state_db,
            snapshot_id=args.snapshot_id,
            frontier_fingerprint_value=args.frontier_fingerprint,
            expected_fencing_token=args.expected_fencing_token,
            resolution=args.resolution,
            evidence_sha256=args.evidence_sha256,
            reconciler=args.reconciler,
            token=os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN"),
        )
    except Exception:
        if external:
            raise ValueError(
                "external queue reconciliation is unavailable or structurally invalid"
            ) from None
        raise

    payload = {
        "mode": "M6_QUEUE_RECONCILIATION",
        "snapshot_id": result.snapshot_id,
        "previous_state": result.previous_state,
        "final_state": result.final_state,
        "fencing_token": result.fencing_token,
        "attempt_generation": result.attempt_generation,
        "resolution": result.resolution,
    }
    if not external:
        payload["frontier_fingerprint"] = result.frontier_fingerprint
    print(json.dumps(payload, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("inventory")

    evaluate = subparsers.add_parser("evaluate-change")
    evaluate.add_argument("--before", type=Path, required=True)
    evaluate.add_argument("--after", type=Path, required=True)
    evaluate.add_argument("--dependencies", type=Path, required=True)

    frontier_summary = subparsers.add_parser("frontier-summary")
    frontier_summary.add_argument("--before", type=Path, required=True)
    frontier_summary.add_argument("--after", type=Path, required=True)
    frontier_summary.add_argument("--dependencies", type=Path, required=True)

    frontier_report = subparsers.add_parser("frontier-report")
    frontier_report.add_argument("--before", type=Path, required=True)
    frontier_report.add_argument("--after", type=Path, required=True)
    frontier_report.add_argument("--dependencies", type=Path, required=True)

    dispatch_report = subparsers.add_parser("dispatch-report")
    dispatch_report.add_argument("--before", type=Path, required=True)
    dispatch_report.add_argument("--after", type=Path, required=True)
    dispatch_report.add_argument("--dependencies", type=Path, required=True)

    wave_plan = subparsers.add_parser("portfolio-wave-plan")
    wave_plan.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    wave_plan.add_argument("--max-parallel", type=int, required=True)
    wave_plan.add_argument("--max-per-identity", type=int, required=True)
    wave_plan.add_argument("--max-per-family", type=int, required=True)
    wave_plan.add_argument(
        "--max-per-lane",
        type=int,
        help="optional capacity ceiling for each explicit/effective lane",
    )
    wave_plan.add_argument(
        "--occupied-collision-key",
        action="append",
        default=[],
    )

    operator_bindings = subparsers.add_parser("portfolio-operator-bindings")
    operator_bindings.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    operator_bindings.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    operator_bindings.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )

    wave_claim = subparsers.add_parser("portfolio-wave-claim")
    wave_claim.add_argument("--plan", type=Path, required=True)
    wave_claim.add_argument(
        "--wave",
        type=Path,
        default=ROOT / "portfolio" / "advancement_wave.public.json",
    )
    wave_claim.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "portfolio" / "corpus.public.json",
    )
    wave_claim.add_argument(
        "--projects",
        type=Path,
        default=ROOT / "registry" / "projects.yaml",
    )
    wave_claim.add_argument("--subject-id", required=True)
    wave_claim.add_argument("--state-db", type=Path, required=True)
    wave_claim.add_argument("--holder", required=True)
    wave_claim.add_argument("--lease-ttl", type=float, required=True)
    wave_claim.add_argument(
        "--allowed-repository",
        action="append",
        default=[],
    )

    wave_promote = subparsers.add_parser("portfolio-wave-promote")
    wave_promote.add_argument("--state-db", type=Path, required=True)
    wave_promote.add_argument("--lineage-id", required=True)
    wave_promote.add_argument("--work-fingerprint", required=True)
    wave_promote.add_argument("--fencing-token", type=int, required=True)
    wave_promote.add_argument("--holder", required=True)
    wave_promote.add_argument("--review", type=Path, required=True)
    wave_promote.add_argument("--execution-grant", type=Path, required=True)
    wave_promote.add_argument("--effect-grant", type=Path)

    source_write_execute = subparsers.add_parser("github-source-write-execute")
    source_write_execute.add_argument("--state-db", type=Path, required=True)
    source_write_execute.add_argument("--lineage-id", required=True)
    source_write_execute.add_argument("--work-fingerprint", required=True)
    source_write_execute.add_argument("--fencing-token", type=int, required=True)

    source_write_qualify = subparsers.add_parser(
        "github-source-write-runtime-qualify"
    )
    source_write_qualify.add_argument("--repository", required=True)
    source_write_qualify.add_argument("--ref", required=True)

    source_write_finalize = subparsers.add_parser(
        "github-source-write-finalize-effect"
    )
    source_write_finalize.add_argument("--state-db", type=Path, required=True)
    source_write_finalize.add_argument("--lineage-id", required=True)
    source_write_finalize.add_argument("--work-fingerprint", required=True)
    source_write_finalize.add_argument("--fencing-token", type=int, required=True)

    source_write_reconcile = subparsers.add_parser(
        "github-source-write-reconcile"
    )
    source_write_reconcile.add_argument("--state-db", type=Path, required=True)
    source_write_reconcile.add_argument("--lineage-id", required=True)
    source_write_reconcile.add_argument("--work-fingerprint", required=True)
    source_write_reconcile.add_argument("--fencing-token", type=int, required=True)
    source_write_reconcile.add_argument("--reconciler", required=True)

    github_smoke = subparsers.add_parser("github-read-smoke")
    github_smoke.add_argument("--repository", required=True)
    github_smoke.add_argument("--ref", required=True)
    github_smoke.add_argument("--expected-head")

    run_inspection = subparsers.add_parser("run-inspection")
    run_inspection.add_argument("--before", type=Path, required=True)
    run_inspection.add_argument("--after", type=Path, required=True)
    run_inspection.add_argument("--dependencies", type=Path, required=True)
    run_inspection.add_argument("--project", required=True)
    run_inspection.add_argument("--frontier-id")
    run_inspection.add_argument("--target-repository", required=True)
    run_inspection.add_argument("--target-ref", required=True)
    run_inspection.add_argument("--target-head", required=True)
    run_inspection.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )
    run_inspection.add_argument("--lineage-id")
    run_inspection.add_argument("--holder", default="project-runner-cli")
    run_inspection.add_argument("--lease-ttl", type=float, default=300.0)

    task_start = subparsers.add_parser("task-start")
    task_start.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )
    task_start.add_argument("--name", required=True)
    task_start.add_argument("--command", dest="task_command", required=True)
    task_start.add_argument("--owner", default="chatgpt")
    task_start.add_argument("--repository")
    task_start.add_argument("--worktree")
    task_start.add_argument("--lane")
    task_start.add_argument("--work-unit")
    task_start.add_argument("--display-command")
    task_start.add_argument(
        "--shell",
        choices=("cmd", "powershell"),
        default="cmd",
    )
    task_start.add_argument(
        "--working-directory",
        type=Path,
        default=Path.cwd(),
    )
    task_start.add_argument(
        "--registration-timeout",
        type=float,
        default=5.0,
    )

    task_register = subparsers.add_parser("task-register")
    task_register.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )
    task_register.add_argument("--name", required=True)
    task_register.add_argument("--pid", type=int, required=True)
    task_register.add_argument("--owner", default="project-runner")
    task_register.add_argument("--repository")
    task_register.add_argument("--worktree")
    task_register.add_argument("--lane")
    task_register.add_argument("--work-unit")
    task_register.add_argument("--command", dest="display_command")
    task_register.add_argument("--shell", choices=("cmd", "powershell"))
    task_register.add_argument("--working-directory")
    task_register.add_argument("--process-started-at-utc")
    task_register.add_argument("--stdout-log")
    task_register.add_argument("--stderr-log")

    task_status = subparsers.add_parser("task-status")
    task_status.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )
    task_status.add_argument("--lane")

    task_finalize = subparsers.add_parser("task-finalize")
    task_finalize.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )
    task_finalize.add_argument("--task-id", required=True)
    task_finalize.add_argument("--exit-code", type=int, required=True)
    task_finalize.add_argument("--terminal-reason", default="PROCESS_EXITED")

    task_history = subparsers.add_parser("task-history")
    task_history.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )
    task_history.add_argument("--lane")

    task_reconcile = subparsers.add_parser("task-reconcile")
    task_reconcile.add_argument(
        "--tasks-root",
        type=Path,
        default=default_tasks_root(),
    )

    operator_status = subparsers.add_parser("operator-status")
    operator_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )
    operator_status.add_argument("--detailed", action="store_true")

    portfolio_cycle = subparsers.add_parser("portfolio-cycle")
    portfolio_cycle.add_argument(
        "--dependencies",
        type=Path,
        default=ROOT / "topology" / "dependencies.yaml",
    )
    portfolio_cycle.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    portfolio_status = subparsers.add_parser("portfolio-status")
    portfolio_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    consume_queue = subparsers.add_parser("consume-queue")
    consume_queue.add_argument(
        "--dependencies",
        type=Path,
        default=ROOT / "topology" / "dependencies.yaml",
    )
    consume_queue.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )
    consume_queue.add_argument("--holder", default="project-runner-queue")
    consume_queue.add_argument("--lease-ttl", type=float, default=300.0)

    queue_status = subparsers.add_parser("queue-status")
    queue_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    worker_route_status = subparsers.add_parser("worker-route-status")
    worker_route_status.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    claim_worker_route = subparsers.add_parser("claim-worker-route")
    claim_worker_route.add_argument("--worker-id", required=True)
    claim_worker_route.add_argument(
        "--route",
        choices=tuple(route.value for route in InvocationRoute),
        required=True,
    )
    claim_worker_route.add_argument("--holder", required=True)
    claim_worker_route.add_argument("--lease-ttl", type=float, default=300.0)
    claim_worker_route.add_argument("--payload-out", type=Path, required=True)
    claim_worker_route.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    run_reference_worker = subparsers.add_parser("run-reference-worker")
    run_reference_worker.add_argument(
        "--holder",
        default="project-runner-reference-read-worker",
    )
    run_reference_worker.add_argument(
        "--lease-ttl",
        type=float,
        default=300.0,
    )
    run_reference_worker.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    record_worker_receipt = subparsers.add_parser("record-worker-receipt")
    record_worker_receipt.add_argument("--route-id", required=True)
    record_worker_receipt.add_argument("--worker-id", required=True)
    record_worker_receipt.add_argument(
        "--route",
        choices=tuple(route.value for route in InvocationRoute),
        required=True,
    )
    record_worker_receipt.add_argument("--holder", required=True)
    record_worker_receipt.add_argument(
        "--expected-fencing-token",
        type=int,
        required=True,
    )
    record_worker_receipt.add_argument(
        "--receipt-class",
        choices=(
            "SUCCEEDED",
            "FAILED_RETRYABLE",
            "FAILED_DETERMINISTIC",
            "OUTCOME_UNKNOWN",
            "SUPERSEDED",
        ),
        required=True,
    )
    record_worker_receipt.add_argument("--receipt-sha256", required=True)
    record_worker_receipt.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    reconcile_queue = subparsers.add_parser("reconcile-queue")
    reconcile_queue.add_argument("--snapshot-id", type=int, required=True)
    reconcile_queue.add_argument("--frontier-fingerprint", required=True)
    reconcile_queue.add_argument(
        "--expected-fencing-token",
        type=int,
        required=True,
    )
    reconcile_queue.add_argument(
        "--resolution",
        choices=(
            "CONFIRM_COMPLETE",
            "CONFIRM_SUPERSEDED",
            "CONFIRM_FAILED_DETERMINISTIC",
            "RELEASE_RETRY_READ_ONLY",
        ),
        required=True,
    )
    reconcile_queue.add_argument("--evidence-sha256", required=True)
    reconcile_queue.add_argument("--reconciler", required=True)
    reconcile_queue.add_argument(
        "--state-db",
        type=Path,
        default=Path(".project-runner/project-runner.sqlite3"),
    )

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate()
    if args.command == "inventory":
        return _inventory()
    if args.command == "evaluate-change":
        return _evaluate_change(args.before, args.after, args.dependencies)
    if args.command == "frontier-summary":
        return _frontier_summary(args.before, args.after, args.dependencies)
    if args.command == "frontier-report":
        return _frontier_report(args.before, args.after, args.dependencies)
    if args.command == "dispatch-report":
        return _dispatch_report(args.before, args.after, args.dependencies)
    if args.command == "portfolio-wave-plan":
        return _portfolio_wave_plan(
            args.wave,
            max_parallel=args.max_parallel,
            max_per_identity=args.max_per_identity,
            max_per_family=args.max_per_family,
            max_per_lane=args.max_per_lane,
            occupied_collision_keys=args.occupied_collision_key,
        )
    if args.command == "portfolio-operator-bindings":
        return _portfolio_operator_bindings(
            args.wave,
            args.corpus,
            args.projects,
        )
    if args.command == "portfolio-wave-claim":
        return _portfolio_wave_claim(
            plan_path=args.plan,
            wave_path=args.wave,
            corpus_path=args.corpus,
            projects_path=args.projects,
            subject_id=args.subject_id,
            state_db=args.state_db,
            holder=args.holder,
            lease_ttl=args.lease_ttl,
            allowed_repositories=args.allowed_repository,
        )
    if args.command == "portfolio-wave-promote":
        return _portfolio_wave_promote(
            state_db=args.state_db,
            lineage_id=args.lineage_id,
            work_fingerprint_value=args.work_fingerprint,
            fencing_token=args.fencing_token,
            holder=args.holder,
            review_path=args.review,
            execution_grant_path=args.execution_grant,
            effect_grant_path=args.effect_grant,
        )
    if args.command == "github-source-write-execute":
        return _github_source_write_execute(
            state_db=args.state_db,
            lineage_id=args.lineage_id,
            work_fingerprint_value=args.work_fingerprint,
            fencing_token=args.fencing_token,
        )
    if args.command == "github-source-write-runtime-qualify":
        return _github_source_write_runtime_qualify(
            args.repository,
            args.ref,
        )
    if args.command == "github-source-write-finalize-effect":
        return _github_source_write_finalize_effect(
            state_db=args.state_db,
            lineage_id=args.lineage_id,
            work_fingerprint_value=args.work_fingerprint,
            fencing_token=args.fencing_token,
        )
    if args.command == "github-source-write-reconcile":
        return _github_source_write_reconcile(
            state_db=args.state_db,
            lineage_id=args.lineage_id,
            work_fingerprint_value=args.work_fingerprint,
            fencing_token=args.fencing_token,
            reconciler=args.reconciler,
        )
    if args.command == "github-read-smoke":
        return _github_read_smoke(
            args.repository,
            args.ref,
            args.expected_head,
        )
    if args.command == "run-inspection":
        return _run_inspection(args)
    if args.command == "task-start":
        return _task_start(args)
    if args.command == "task-register":
        return _task_register(args)
    if args.command == "task-status":
        return _task_status(args)
    if args.command == "task-finalize":
        return _task_finalize(args)
    if args.command == "task-history":
        return _task_history(args)
    if args.command == "task-reconcile":
        return _task_reconcile(args)
    if args.command == "operator-status":
        return _operator_status(args)
    if args.command == "portfolio-cycle":
        return _portfolio_cycle(args)
    if args.command == "portfolio-status":
        return _portfolio_status(args)
    if args.command == "consume-queue":
        return _consume_queue(args)
    if args.command == "queue-status":
        return _queue_status(args)
    if args.command == "worker-route-status":
        return _worker_route_status(args)
    if args.command == "claim-worker-route":
        return _claim_worker_route(args)
    if args.command == "record-worker-receipt":
        return _record_worker_receipt(args)
    if args.command == "run-reference-worker":
        return _run_reference_worker(args)
    if args.command == "reconcile-queue":
        return _reconcile_queue(args)
    parser.error(f"unhandled command: {args.command}")


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
