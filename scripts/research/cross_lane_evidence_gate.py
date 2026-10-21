"""Cross-lane claim ceiling: structural rejection, never external attestation.

This module intentionally cannot authenticate a Git head, witness, evaluator,
host, installed process or authorization. All input is caller-controlled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

STAGES = (
    "SOURCE", "BUILD", "INSTALLED_ARTIFACT", "SELECTED_ROUTE",
    "RUNTIME_CONSUMPTION", "OBSERVABLE_BEHAVIOR",
    "INDEPENDENT_REVIEW", "AUTHORIZED_OPERATION",
)
REQUIRED = {
    "SOURCE": ("source_digest",),
    "BUILD": ("build_artifact_digest", "build_source_digest"),
    "INSTALLED_ARTIFACT": ("installed_artifact_digest",),
    "SELECTED_ROUTE": ("route_target_digest",),
    "RUNTIME_CONSUMPTION": ("consumer_observation_digest",),
    "OBSERVABLE_BEHAVIOR": ("behavior_probe_digest",),
    "INDEPENDENT_REVIEW": ("review_subject_digest", "review_evidence_digest"),
    "AUTHORIZED_OPERATION": ("authority_subject_digest", "effect_readback_digest"),
}


@dataclass(frozen=True)
class Ceiling:
    requested: str
    structurally_supported_through: str | None
    status: str
    missing: tuple[str, ...]
    limitations: tuple[str, ...]


def assess_claim(requested: str, evidence: Mapping[str, object]) -> Ceiling:
    """Assess structural completeness without converting assertions to proof.

    Evidence MUST be authenticated by an independent, external process before
    any stage can be called verified. In particular an arbitrary digest-looking
    string, flag, reported PID, or declared evaluator is never proof.
    """
    if requested not in STAGES:
        raise ValueError("unknown stage")
    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping")
    missing: list[str] = []
    last: str | None = None
    for stage in STAGES[: STAGES.index(requested) + 1]:
        keys = REQUIRED[stage]
        absent = [k for k in keys if not isinstance(evidence.get(k), str)
                  or not evidence[k].strip()]
        if absent:
            missing.extend(absent)
            break
        last = stage
    limitations = (
        "CALLER_CONTROLLED_FIELDS_NOT_AUTHENTICATED",
        "NO_VERIFIED_INDEPENDENT_WITNESS",
        "NO_RUNTIME_OR_EFFECT_AUTHORITY_FROM_THIS_ASSESSMENT",
    )
    return Ceiling(
        requested=requested,
        structurally_supported_through=last,
        status="HOLD_MISSING_EVIDENCE" if missing else "HOLD_EXTERNAL_VERIFICATION",
        missing=tuple(missing),
        limitations=limitations,
    )


def assess_metacognition_comparison(arms: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    """Negative structural gate for alleged method improvements, not a scorer."""
    reasons: list[str] = []
    if not isinstance(arms, Mapping) or set(arms) != {"candidate", "control"}:
        return "HOLD", ("MISSING_MATCHED_ARMS",)
    case_ids = []
    for name in ("candidate", "control"):
        arm = arms[name]
        if not isinstance(arm, Mapping):
            reasons.append(f"{name}:INVALID_ARM")
            continue
        ids = arm.get("case_ids")
        if not isinstance(ids, list) or not ids or any(
            not isinstance(x, str) or not x for x in ids
        ) or len(set(ids)) != len(ids):
            reasons.append(f"{name}:INVALID_CASE_IDENTITIES")
            continue
        case_ids.append(tuple(ids))
        if arm.get("presented") != len(ids):
            reasons.append(f"{name}:UNMATCHED_DENOMINATOR")
        a, b = arm.get("answered"), arm.get("abstained")
        if type(a) is not int or type(b) is not int or a < 0 or b < 0 or a + b != len(ids):
            reasons.append(f"{name}:INCOHERENT_COVERAGE")
        if arm.get("source_group") in (None, ""):
            reasons.append(f"{name}:UNBOUND_SOURCE_GROUP")
        if arm.get("resource_budget") in (None, ""):
            reasons.append(f"{name}:UNBOUND_BUDGET")
    if len(case_ids) == 2 and case_ids[0] != case_ids[1]:
        reasons.append("UNMATCHED_CASE_ORDER_OR_IDS")
    if not reasons:
        reasons.append("INDEPENDENT_LABEL_CUSTODY_AND_CAUSAL_USE_UNVERIFIED")
    return "HOLD", tuple(reasons)
