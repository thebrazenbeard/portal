"""Separate synthetic validation before retaining any provisional Goal 1 skill.

An in-process scorer is not an independently attested evaluator. This helper
ties already-published rule commitments, evidence arbitration, and the
one-shot blind scorer together; it does NOT train weights, promote canonical
memories, invoke QVR, or grant authority to a proposed skill.
"""
from __future__ import annotations

from dataclasses import dataclass

from goal1_blind_inquiry_gate import BlindInquiryGate
from goal1_evidence_arbitration import ArbitrationResult, CLAIM as ARBITRATION_CLAIM
from goal1_rule_proposals import RuleCommitment, predict_rules

CEILING = "SYNTHETIC_SEPARATE_HOLDOUT_VALIDATION_NOT_INDEPENDENT_QUALIFICATION"


@dataclass(frozen=True, slots=True)
class SkillValidationResult:
    status: str
    selected_rule: str | None
    validated_correct: int
    validated_attempted: int
    validation_prediction_sha256: str | None
    eligible_for_noncanonical_retention: bool
    qualified_as_self_learning: bool = False
    claim_ceiling: str = CEILING


def validate_candidate(
    *,
    commitment: RuleCommitment,
    arbitration: ArbitrationResult,
    validation_gate: BlindInquiryGate,
    expected_commitment: str,
    expected_arbitration_fingerprint: str,
) -> SkillValidationResult:
    """Require fresh, one-shot validation separate from inquiry observations.

    The caller must custody the evaluator and pre-register its heldout samples
    before seeing the candidate; this code cannot prove either condition.
    """
    if (type(commitment) is not RuleCommitment
            or type(arbitration) is not ArbitrationResult
            or type(validation_gate) is not BlindInquiryGate):
        raise ValueError("expected exact synthetic research types")
    if (type(expected_commitment) is not str
            or expected_commitment != commitment.commitment_sha256
            or type(expected_arbitration_fingerprint) is not str
            or arbitration.fingerprint != expected_arbitration_fingerprint
            or arbitration.claim_ceiling != ARBITRATION_CLAIM):
        raise ValueError("unbound or unexpected precommit")
    if validation_gate.closed:
        from goal1_blind_inquiry_gate import TrialClosed
        raise TrialClosed("validation already completed")
    if validation_gate.probes_used != 0:
        raise ValueError("validation source contaminated by inquiry probes")
    if (arbitration.status != "ONE_SURVIVING_HYPOTHESIS"
            or len(arbitration.remaining) != 1):
        return SkillValidationResult(
            status="NO_UNIQUE_RULE",
            selected_rule=None,
            validated_correct=0,
            validated_attempted=0,
            validation_prediction_sha256=None,
            eligible_for_noncanonical_retention=False,
        )
    rule = arbitration.remaining[0]
    if rule not in commitment.rules:
        raise ValueError("arbitrated rule not in frozen proposal")
    validation_inputs = validation_gate.open_holdout()
    predicted = predict_rules(
        commitment,
        validation_inputs,
        expected_commitment=expected_commitment,
    )[rule]
    score = validation_gate.commit_predictions(predicted)
    correct = score["correct"]
    attempted = score["attempted"]
    eligible = attempted > 0 and correct == attempted
    return SkillValidationResult(
        status="PROVISIONAL_PASS_UNQUALIFIED" if eligible else "REJECTED_BY_NEW_EVIDENCE",
        selected_rule=rule,
        validated_correct=correct,
        validated_attempted=attempted,
        validation_prediction_sha256=score["prediction_sha256"],
        eligible_for_noncanonical_retention=eligible,
    )
