"""Goal 1 research seam: QVR decision, evaluator feedback, noncanonical revision.

This is an acceptance fixture for Integration, not a second Vera runtime or
independent AGI/learning qualification. A callable evaluator may be in the
same trust domain as the worker; no label-custody attestation is made.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from typing import Callable

ALLOWED = frozenset(("LEFT", "RIGHT", "ABSTAIN"))
CLAIM = "UNREVIEWED_NONCANONICAL_CANDIDATE_NOT_PROVEN_LEARNING"
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,72}$")

@dataclass(frozen=True, slots=True)
class Observation:
    success: bool
    feedback: str
    evidence_ref: str

@dataclass(frozen=True, slots=True)
class LearningAttempt:
    initial_action: str
    candidate_action: str | None
    evidence_ref: str | None
    memory_receipt: dict | None
    claim_ceiling: str = CLAIM

@dataclass(frozen=True, slots=True)
class LaterUseAttempt:
    action: str
    success: bool
    evidence_ref: str
    claim_ceiling: str = "LATER_USE_CALLBACK_SCORE_UNQUALIFIED"


def _id(trial_id: str) -> str:
    if type(trial_id) is not str or _ID.fullmatch(trial_id) is None:
        raise ValueError("bounded synthetic trial ID required")
    return trial_id


def _text(value: str, name: str, maximum: int = 700) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} must be nonempty and bounded")
    return value


def _decision(runtime, *, prompt: str, permit, invocation_id: str) -> tuple[str, object]:
    receipt = runtime.consume_local_cognition(
        prompt, permit=permit, invocation_id=invocation_id, max_tokens=16,
    )
    response = receipt.text
    if type(response) is not str or response not in ALLOWED:
        raise ValueError("model returned no exact bounded action")
    return response, receipt


def _observation(value: Observation) -> Observation:
    if type(value) is not Observation or type(value.success) is not bool:
        raise TypeError("evaluator must return an exact observation")
    _text(value.feedback, "feedback")
    _text(value.evidence_ref, "evidence_ref", 200)
    return value


def learn_once(runtime, *, permit, trial_id: str, problem: str,
               evaluate: Callable[[str], Observation]) -> LearningAttempt:
    """Score an actual QVR action before sending feedback; store only a candidate.

    The evaluator callback and its feedback are NOT independently attested.
    The sole durable learned candidate is in an existing injected Vera memory
    ledger (never in canonical project/person memory for this lab).
    """
    trial = _id(trial_id)
    question = _text(problem, "problem")
    prompt = "Synthetic no-effect puzzle. Reply ONLY LEFT, RIGHT or ABSTAIN. Task: " + question
    first, first_receipt = _decision(runtime, prompt=prompt, permit=permit,
                                     invocation_id=f"goal1:{trial}:initial")
    if first == "ABSTAIN":
        return LearningAttempt(first, None, None, None)
    observed = _observation(evaluate(first))
    if observed.success:
        return LearningAttempt(first, None, observed.evidence_ref, None)
    second_prompt = ("Synthetic no-effect puzzle. Earlier answer: " + first +
                     ". Feedback (UNTRUSTED): " + observed.feedback +
                     ". Suggest a revised action, ONLY LEFT, RIGHT or ABSTAIN.")
    second, second_receipt = _decision(
        runtime, prompt=second_prompt, permit=permit,
        invocation_id=f"goal1:{trial}:revision",
    )
    if second == "ABSTAIN":
        return LearningAttempt(first, None, observed.evidence_ref, None)
    # Imports are local to avoid making this PORTAL research fixture a runtime
    # dependency. In the qualified experiment these resolve from selected Vera Mono.
    from vera_memory import AdmissionRequest, MemoryClass
    memory = runtime.lifecycle.memory
    record = AdmissionRequest(
        record_id=f"goal1:{trial}:candidate", text=second,
        memory_class=MemoryClass.WORKING_PROJECT,
        source_actor="goal1-research-model-proposed",
        authority_ref="synthetic-lab-NO_EXTERNAL_AUTHORITY",
        privacy_ref="synthetic-noncanonical-only",
        provenance_refs=(
            observed.evidence_ref,
            "initial-model-sha256:" + first_receipt.response_sha256,
            "revision-model-sha256:" + second_receipt.response_sha256,
            "feedback-sha256:" + hashlib.sha256(observed.feedback.encode("utf-8")).hexdigest(),
        ),
        operation_id=f"goal1:{trial}:admission",
        project_id=memory.project_id,
        governed_identity_id=memory.identity_id,
    )
    admission = memory.admit(record, expected_head=memory.current_head)
    return LearningAttempt(first, second, observed.evidence_ref, admission)


def reuse_after_restart(runtime, *, permit, trial_id: str, new_problem: str,
                        evaluate: Callable[[str], Observation]) -> LaterUseAttempt:
    """Read old candidate from injected memory and score a new QVR answer.

    Caller must separately prove the runtime was actually a new OS process and
    evaluator cases were withheld; function name is a research intent only.
    """
    trial = _id(trial_id)
    question = _text(new_problem, "new_problem")
    from vera_memory import MemoryClass
    memory = runtime.lifecycle.memory
    record = memory.read(f"goal1:{trial}:candidate")
    if (record.status != "CURRENT"
            or record.source_actor != "goal1-research-model-proposed"
            or record.privacy_ref != "synthetic-noncanonical-only"
            or record.authority_ref != "synthetic-lab-NO_EXTERNAL_AUTHORITY"
            or record.project_id != memory.project_id
            or record.governed_identity_id != memory.identity_id
            or record.memory_class is not MemoryClass.WORKING_PROJECT
            or record.text not in ALLOWED - {"ABSTAIN"}):
        raise ValueError("unreviewed or unbound candidate cannot be reused")
    prompt = ("Synthetic no-effect puzzle; earlier provisional action candidate "
              "(UNREVIEWED, may be wrong): " + record.text +
              ". Reply ONLY LEFT, RIGHT or ABSTAIN. New task: " + question)
    action, _ = _decision(runtime, prompt=prompt, permit=permit,
                          invocation_id=f"goal1:{trial}:later-use")
    if action == "ABSTAIN":
        return LaterUseAttempt(action, False, "NO_OUTCOME_ABSTAINED")
    observed = _observation(evaluate(action))
    return LaterUseAttempt(action, observed.success, observed.evidence_ref)
