"""Executable SOURCE-ONLY owner proposal; not a production producer/admitter.

Uses existing canonical delegation and native immutable WorkUnit identity.
No task, claim, trust, collision or execution authority is created. The current
official producer does not consume this contract. Every receipt remains HOLD.
"""
from dataclasses import replace
import json

from runner.work_units import WorkUnitStatus, work_unit_fingerprint

KEY = "UNAPPROVED_MONO_PRODUCER_ASSOCIATION_V1"


def _detach(value):
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False))


def _association(runtime, delegation, actor_ref, expected_task_head, work):
    if type(actor_ref) is not str or not actor_ref:
        raise ValueError("explicit untrusted actor label required")
    runtime.validate_task_delegation(delegation, actor_ref=actor_ref)
    state = runtime.tasks.read(delegation.task_id)
    events = runtime.tasks.events(delegation.task_id)
    if not events or events[-1].event_digest != expected_task_head:
        raise ValueError("stale task-scoped producer snapshot")
    if state.delegation(delegation.delegation_id).allowed_effects:
        raise ValueError("proposal permits no delegated effects")
    if (len(work.inputs) != 1 or work.inputs[0].repository != delegation.repository
            or work.inputs[0].ref != delegation.ref):
        raise ValueError("producer target scope differs from canonical delegation")
    if (work.operation != "PORTFOLIO_BOUND_CLAIM" or work.status is not WorkUnitStatus.PENDING
            or work.payload.get("execution_authority") is not False
            or work.payload.get("protected_effects_authorized") is not False):
        raise ValueError("proposal requires unadmitted NO_EFFECT work")
    # Lifecycle evidence is kept under its actual ABI name: arbitrary qualified
    # tasks must not have this silently relabelled as authenticated user input.
    association = {"schema": KEY, "task_id": state.task_id,
        "task_packet_digest": state.packet.packet_digest,
        "opened_lifecycle_evidence_digest": state.opened_lifecycle_evidence_digest,
        "original_text": state.packet.subject,
        "opened_head": events[0].event_digest, "expected_task_head": expected_task_head,
        "delegation": delegation.canonical_body(),
        "target": {"repository": work.inputs[0].repository, "ref": work.inputs[0].ref,
            "exact_head": work.inputs[0].commit},
        "producer_authenticated": False, "effect": "NO_EFFECT"}
    return _detach(association), state.journal_head_digest


def propose(*, runtime, delegation, actor_ref, expected_task_head, work):
    """Derive and seal BEFORE native fingerprinting; never initialize/admit."""
    with runtime.tasks.action_lock():
        association, global_head = _association(runtime, delegation, actor_ref, expected_task_head, work)
        payload = _detach(work.payload)
        if KEY in payload:
            raise ValueError("caller must not pre-insert producer association")
        payload[KEY] = _detach(association)
        proposed = replace(work, payload=payload)
        return {"work": proposed, "association": association,
            "fingerprint": work_unit_fingerprint(proposed), "global_head_observed": global_head}


def revalidate(*, runtime, delegation, actor_ref, expected_task_head, proposal):
    """Re-read actual task/ref, detect mutable payload substitution, still HOLD.

    This is not a two-store atomic admission. A later official producer would
    need owner-approved lock ordering/crash protocol and authentic domain proof.
    Caller-authored public hashes or a copied root cannot supply those things.
    """
    with runtime.tasks.action_lock():
        work = proposal["work"]
        if work_unit_fingerprint(work) != proposal["fingerprint"]:
            raise ValueError("producer snapshot fingerprint changed")
        actual, global_head = _association(runtime, delegation, actor_ref, expected_task_head, work)
        if work.payload.get(KEY) != actual or proposal["association"] != actual:
            raise ValueError("producer association differs from canonical task")
        return {"state": "HOLD", "effect": "NO_EFFECT", "producer_authenticated": False,
            "dispatch_authorized": False, "task_id": actual["task_id"],
            "expected_task_head": expected_task_head, "global_head_observed": global_head,
            "work_fingerprint": proposal["fingerprint"],
            "reasons": ["UNAPPROVED_PRODUCER_ABI", "CLAIM_COLLISION_DOMAIN_UNQUALIFIED"]}


def inspect_observed_local_work(
    *, runtime, delegation, actor_ref, expected_task_head, proposal, observed_work,
):
    """SOURCE-ONLY inspection of an ordinary LOCAL Runner WorkUnit.

    A valid task snapshot and an observed WorkUnit digest are not an authenticated
    Mono producer, receiver-owned nonce, or cross-root collision authority.
    This function neither reads nor mutates the Runner store or permits dispatch.
    """
    from vera_core import TaskExecutionError

    try:
        # Re-read canonical task-specific event head; GLOBAL changes alone do
        # not invalidate a task-local proposal. No two-store atomicity is implied.
        source = revalidate(
            runtime=runtime, delegation=delegation, actor_ref=actor_ref,
            expected_task_head=expected_task_head, proposal=proposal,
        )
    except (ValueError, TaskExecutionError):
        source = None

    matched = bool(
        source is not None
        and work_unit_fingerprint(observed_work) == source["work_fingerprint"]
    )
    reasons = ["LOCAL_CLAIM_NOT_CANONICAL_AUTHORITY", "NO_SELECTED_MONO_RECEIVER_ISSUER",
               "MONO_TARGET_COMMIT_UNSELECTED"]
    if source is None:
        reasons.append("TASK_SOURCE_UNVERIFIED")
    if not matched:
        reasons.append("OBSERVED_WORK_NOT_SEALED_PROPOSAL")

    # These are source-level observations, not authentication assertions.
    return {
        "state": "HOLD",
        "effect": "NO_EFFECT",
        "target_commit_authorized": False,
        "producer_authenticated": False,
        "dispatch_authorized": False,
        "memory_admitted": False,
        "source_proposal_revalidated": source is not None,
        "observed_work_matches_proposal": matched,
        "reasons": reasons,
    }
