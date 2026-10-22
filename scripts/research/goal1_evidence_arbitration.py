"""Bounded evidence arbitration for a provisional Goal 1 research skill.

This is not a learner that invents rules or a substitute for QVR, VeraMemory,
review authority, or an independently held truth set. A separate model may
propose hypotheses and choose probes; only exact evaluator observations may
eliminate hypotheses. Never execute model-authored code or elevate a proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")
_LABELS = frozenset(("0", "1"))
_REF = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9:._/-]{0,199}\\Z")
CLAIM = "SYNTHETIC_DEVELOPER_SUPPLIED_HYPOTHESIS_ARBITRATION"


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ArbitrationResult:
    case_id: str
    fingerprint: str
    evidence_digest: str
    status: str
    remaining: tuple[str, ...]
    heldout_predictions: Mapping[str, str]
    abstained: int
    observations_used: int
    claim_ceiling: str = CLAIM


def reconcile(
    *,
    case_id: str,
    predictions: dict[str, dict[str, str]],
    training_ids: tuple[str, ...],
    holdout_ids: tuple[str, ...],
    observed: tuple[tuple[str, str, str], ...],
    expected_fingerprint: str | None = None,
) -> ArbitrationResult:
    """Return only unanimous held-out labels after finite scoped observations.

    Candidate predictions are caller-supplied and may be wrong; their
    completeness, literal values, and source need separate qualification.
    A fingerprint checks a supplied commitment, not independent attestation.
    """
    _id(case_id, "case_id")
    if (type(predictions) is not dict or not 2 <= len(predictions) <= 12
            or type(training_ids) is not tuple or type(holdout_ids) is not tuple
            or type(observed) is not tuple or len(observed) > 16):
        raise ValueError("invalid bounded arbitration arguments")
    if (not training_ids or not holdout_ids
            or not 1 <= len(training_ids) + len(holdout_ids) <= 96):
        raise ValueError("invalid case partition size")
    for group in (training_ids, holdout_ids):
        if len(set(group)) != len(group):
            raise ValueError("repeated case ID")
        for item in group:
            _id(item, "case item")
    train = frozenset(training_ids)
    withheld = frozenset(holdout_ids)
    if train & withheld:
        raise ValueError("training and holdout overlap")
    keys = train | withheld
    checked: dict[str, dict[str, str]] = {}
    for hypothesis, values in predictions.items():
        _id(hypothesis, "hypothesis")
        if type(values) is not dict or set(values) != keys:
            raise ValueError("candidate labels missing or extra")
        for k, label in values.items():
            if type(label) is not str or label not in _LABELS:
                raise ValueError("candidate label invalid")
        checked[hypothesis] = dict(values)
    fingerprint = _digest({
        "case_id": case_id,
        "training_ids": sorted(train),
        "withheld_ids": sorted(withheld),
        "hypotheses": checked,
    })
    if expected_fingerprint is not None and (
        type(expected_fingerprint) is not str or
        expected_fingerprint != fingerprint
    ):
        raise ValueError("candidate commitment mismatch")
    normalized: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for observation in observed:
        if type(observation) is not tuple or len(observation) != 3:
            raise ValueError("bad observation shape")
        item, label, ref = observation
        if (type(item) is not str or item not in train or item in seen
                or type(label) is not str or label not in _LABELS
                or type(ref) is not str or not ref or len(ref) > 200):
            raise ValueError("invalid or duplicate observation")
        if _REF.fullmatch(ref) is None:
            raise ValueError("invalid observation ref")
        seen.add(item)
        normalized.append((item, label, ref))
    remaining = tuple(sorted(
        hypothesis for hypothesis, values in checked.items()
        if all(values[item] == label for item, label, _ in normalized)
    ))
    accepted: dict[str, str] = {}
    for item in sorted(withheld):
        unanimous = {checked[choice][item] for choice in remaining}
        if len(unanimous) == 1 and remaining:
            accepted[item] = next(iter(unanimous))
    if not remaining:
        status = "CONTRADICTION"
    elif len(remaining) == 1:
        status = "ONE_SURVIVING_HYPOTHESIS"
    else:
        status = "UNRESOLVED"
    return ArbitrationResult(
        case_id=case_id,
        fingerprint=fingerprint,
        evidence_digest=_digest(normalized),
        status=status,
        remaining=remaining,
        heldout_predictions=MappingProxyType(accepted),
        abstained=len(withheld) - len(accepted),
        observations_used=len(normalized),
    )
