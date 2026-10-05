from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import (
    SqliteDispatchAdmissionStore,
    execute_admitted,
    verify_observed_attempt,
)
from runner.frontier import derive_frontiers
from runner.github_backend import (
    GitHubBackend,
    GitHubOperation,
    GitHubRestTransport,
    TargetAuthorityGrant,
)
from runner.m6_github import (
    GitHubCurrentSubjectReader,
    GitHubReadInspectionBackend,
    frontier_to_github_inspection_work,
)
from runner.models import ExactSubject, FrontierStatus
from runner.persistent_state import SqliteBudgetStore, SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.propagate import derive_invalidations
from runner.registry import load_dependencies, load_observations, load_project_snapshot
from runner.work_units import WorkUnitStatus, work_unit_fingerprint


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SUBSTANTIVE = FIXTURES / "m6-hc-substantive.yaml"
CURRENT = FIXTURES / "m6-hc-current.yaml"
DEPENDENCIES = FIXTURES / "m6-hc-transcendence-dependencies.yaml"

TRANSCENDENCE_SUBJECT = ExactSubject(
    repository="thebrazenbeard/transcendence",
    ref="architecture/consciousness-backup-v1",
    commit="ca3965c5788cd1bbb52fc527e47e3bcd5eca6e4a",
)


def _budget() -> BudgetEnvelope:
    return BudgetEnvelope(
        lineage_id="m6-hc-transcendence-live-proof",
        max_depth=1,
        depth=0,
        remaining_children=0,
        remaining_active=1,
        remaining_retries=0,
        remaining_backend_jobs=1,
    )


def _github_backend(token: str | None) -> GitHubBackend:
    return GitHubBackend(
        transport=GitHubRestTransport(token=token),
        route_capabilities=("github.read_ref",),
        grants=(
            TargetAuthorityGrant(
                repository="thebrazenbeard/hc-brain",
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=("main",),
            ),
            TargetAuthorityGrant(
                repository="thebrazenbeard/transcendence",
                operations=(GitHubOperation.READ_REF,),
                ref_prefixes=("architecture/consciousness-backup-v1",),
            ),
        ),
    )


def main() -> int:
    token = os.environ.get("PROJECT_RUNNER_GITHUB_TOKEN")
    previous = load_observations(SUBSTANTIVE)
    fixture_current = load_observations(CURRENT)

    live_reader = GitHubCurrentSubjectReader(_github_backend(token))
    hc_seed = next(
        observation.subject
        for observation in fixture_current
        if observation.target == "hc-brain"
    )
    live_hc = live_reader.read(hc_seed)
    if live_hc is None or live_hc.commit is None:
        raise RuntimeError("live HC currentness snapshot is unavailable")
    current = tuple(
        replace(
            observation,
            subject=live_hc,
            observed_value=live_hc.commit,
            observer="github/hc-brain-main-live-proof",
        )
        if observation.target == "hc-brain"
        else observation
        for observation in fixture_current
    )

    dependencies = load_dependencies(DEPENDENCIES)
    registry_snapshot = load_project_snapshot(ROOT / "registry" / "projects.yaml")
    invalidations = derive_invalidations(previous, current, dependencies)
    frontiers = derive_frontiers(
        invalidations,
        capability_lookup={"transcendence": {"analyze"}},
    )
    if len(frontiers) != 1 or frontiers[0].status is not FrontierStatus.READY:
        raise RuntimeError("expected exactly one ready HC to Transcendence frontier")

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "m6-live.sqlite3"
        budget_store = SqliteBudgetStore(db)
        generation = budget_store.put_initial(_budget())
        persisted_budget, observed_generation = budget_store.get(
            "m6-hc-transcendence-live-proof"
        )
        if observed_generation != generation:
            raise RuntimeError("budget generation changed before dispatch")

        lease_store = SqliteLeaseStore(db)
        recursive_store = SqliteRecursiveWorkStore(db)
        execution_backend = GitHubReadInspectionBackend(_github_backend(token))

        work = frontier_to_github_inspection_work(
            frontiers[0],
            TRANSCENDENCE_SUBJECT,
            persisted_budget.depth,
            registry_digest=registry_snapshot.sha256,
        )
        fingerprint = work_unit_fingerprint(work)
        recursive_store.put_initial(
            work=work,
            lineage_id=persisted_budget.lineage_id,
            budget_scope_id=persisted_budget.scope_id,
            parent_fingerprint=None,
            ancestry_fingerprints={fingerprint},
            effective_capabilities={"read", "analyze"},
        )

        dispatch_store = SqliteDispatchAdmissionStore(db)
        admitted = dispatch_store.admit(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=fingerprint,
            budget_scope_id=persisted_budget.scope_id,
            expected_budget_generation=generation,
            expected_work_generation=1,
            holder="github-actions-m6-live-proof",
            now=0.0,
            ttl=60.0,
        )
        new_generation = admitted.budget_generation
        if admitted.budget_after.remaining_active != 0:
            raise RuntimeError("durable active budget was not reserved before execution")
        if admitted.budget_after.remaining_backend_jobs != 0:
            raise RuntimeError("durable backend budget was not reserved before execution")
        admitted_entries = dispatch_store.unresolved_attempts()
        if len(admitted_entries) != 1 or admitted_entries[0].phase != "ADMITTED":
            raise RuntimeError("durable execution journal did not record admission")

        attempt = execute_admitted(
            frontier=frontiers[0],
            admission=admitted,
            backend=execution_backend,
            journal=dispatch_store,
            recorded_at=0.1,
        )
        result_entries = dispatch_store.unresolved_attempts()
        if len(result_entries) != 1 or result_entries[0].phase != "RESULT_RECORDED":
            raise RuntimeError("durable execution result did not survive journal write")
        running_work = recursive_store.compare_and_swap_status(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=work_unit_fingerprint(attempt.work),
            expected_generation=admitted.work_generation,
            status=WorkUnitStatus.RUNNING,
            lease=attempt.lease,
            now=0.25,
        )
        if running_work.generation != 3:
            raise RuntimeError("durable recursive work did not enter RUNNING")
        verifying_work = recursive_store.compare_and_swap_status(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=work_unit_fingerprint(attempt.work),
            expected_generation=3,
            status=WorkUnitStatus.VERIFYING,
            lease=attempt.lease,
            now=0.5,
        )
        if verifying_work.generation != 4:
            raise RuntimeError("durable recursive work did not enter VERIFYING")

        independent_reader = GitHubCurrentSubjectReader(_github_backend(token))
        outcome = verify_observed_attempt(
            attempt,
            lease_store=lease_store,
            now=1.0,
            current_subject_reader=independent_reader.read,
            evidence_verifier=lambda work, result: (
                result.succeeded
                and result.outputs == ("READ_REF_OK", "READ_REF_OK")
                and result.evidence
                == (
                    "m6:github-read-inspection",
                    "github:readback-verified",
                    "subjects=2",
                )
            ),
            manage_lease=False,
        )
        if outcome.status is not WorkUnitStatus.COMPLETE:
            raise RuntimeError(
                "live inspection did not complete: "
                f"{outcome.status.value} "
                f"(backend={attempt.result.classification})"
            )
        if dispatch_store.unresolved_attempts()[0].phase != "RESULT_RECORDED":
            raise RuntimeError("terminal state was promoted before atomic finalization")
        if dispatch_store.load_result(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=attempt.lease.fencing_token,
        ) != attempt.result:
            raise RuntimeError("durable execution result changed before finalization")

        terminal_generation = dispatch_store.finalize_terminal_verification(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=attempt.lease.fencing_token,
            expected_work_generation=4,
            lease=attempt.lease,
            status=outcome.status,
            reason=outcome.reason,
            verified_at=1.0,
        )
        if terminal_generation != 5:
            raise RuntimeError("atomic terminal generation did not reach five")
        durable_work = recursive_store.get(
            persisted_budget.lineage_id,
            fingerprint,
        )
        if durable_work is None or durable_work.generation != 5:
            raise RuntimeError("durable recursive work did not atomically finalize")
        if durable_work.work.status is not WorkUnitStatus.COMPLETE:
            raise RuntimeError("atomic terminal work status is not COMPLETE")
        if dispatch_store.unresolved_attempts():
            raise RuntimeError("terminal verification did not close execution journal")

        dispatch_store.close()
        budget_store.close()
        lease_store.close()
        recursive_store.close()

        reopened_budget = SqliteBudgetStore(db)
        stored_budget, stored_generation = reopened_budget.get(
            "m6-hc-transcendence-live-proof"
        )
        if stored_generation != new_generation:
            raise RuntimeError("durable budget generation did not survive reopen")
        if stored_budget.remaining_active != 0:
            raise RuntimeError("durable active budget was not consumed")
        if stored_budget.remaining_backend_jobs != 0:
            raise RuntimeError("durable backend budget was not consumed")
        reopened_budget.close()

        reopened_journal = SqliteDispatchAdmissionStore(db)
        if reopened_journal.unresolved_attempts():
            raise RuntimeError("execution journal reopened with unresolved terminal work")
        if reopened_journal.load_result(
            lineage_id=persisted_budget.lineage_id,
            work_fingerprint_value=fingerprint,
            fencing_token=attempt.lease.fencing_token,
        ) != attempt.result:
            raise RuntimeError("execution result changed across restart")
        reopened_journal.close()

        reopened_recursive = SqliteRecursiveWorkStore(db)
        resumed_work = reopened_recursive.get(
            persisted_budget.lineage_id,
            work_unit_fingerprint(attempt.work),
        )
        if resumed_work is None:
            raise RuntimeError("durable recursive work disappeared after restart")
        if resumed_work.work != replace(
            attempt.work,
            status=WorkUnitStatus.COMPLETE,
        ):
            raise RuntimeError("durable recursive work changed across restart")
        if resumed_work.work.status is not WorkUnitStatus.COMPLETE:
            raise RuntimeError("durable recursive work status did not survive restart")
        if resumed_work.ancestry_fingerprints != {
            work_unit_fingerprint(attempt.work)
        }:
            raise RuntimeError("durable recursive root ancestry changed across restart")
        recursive_generation = resumed_work.generation
        reopened_recursive.close()

    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "reaction": frontiers[0].work_type,
                "provider_head": frontiers[0].subject.commit,
                "consumer_head": TRANSCENDENCE_SUBJECT.commit,
                "budget_generation": new_generation,
                "recursive_work_generation": recursive_generation,
                "recursive_work_status": WorkUnitStatus.COMPLETE.value,
                "fencing_token": attempt.lease.fencing_token,
                "execution_journal": "COMPLETE",
                "route": "github.read_ref",
                "project_registry_sha256": registry_snapshot.sha256,
                "subjects_verified": 2,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
