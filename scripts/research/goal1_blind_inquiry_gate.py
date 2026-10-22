"""Minimal subject-blind inquiry scoring research seam for Vera Goal 1.

This is a developer-owned, in-process synthetic evaluator. It is *not* a
security isolation mechanism, an independently qualified hidden test set, a
self-learning algorithm, or evidence that a selected QVR/model was used.

The subject-facing adapter may call public_prompt(), probe(id), open_holdout(),
commit_predictions(predictions); it must never pass the evaluator instance,
answer mapping or hidden configuration to a model.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from types import MappingProxyType

_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")
_VALUE = frozenset(("0", "1"))
CLAIM_CEILING = "DEVELOPER_HELD_SYNTHETIC_ORACLE_NOT_MODEL_LEARNING"


class ProbeDenied(ValueError):
    """The proposed query is not a valid, available, budgeted observation."""


class TrialClosed(ValueError):
    """The learner cannot rerun or retroactively change an evaluation."""


class BlindInquiryGate:
    """One finite test-time inquiry stream with delayed, one-shot scoring."""

    def __init__(self, *, case_id: str, inputs: Mapping[str, str],
                 answers: Mapping[str, str], training_ids: tuple[str, ...],
                 holdout_ids: tuple[str, ...], probe_budget: int):
        if type(case_id) is not str or _ID.fullmatch(case_id) is None:
            raise ValueError("invalid case ID")
        if type(probe_budget) is not int or not 0 <= probe_budget <= 12:
            raise ValueError("invalid probe budget")
        if (type(inputs) is not dict or type(answers) is not dict
                or type(training_ids) is not tuple or type(holdout_ids) is not tuple):
            raise TypeError("expected exact bounded fixture types")
        if (not inputs or len(inputs) > 64 or set(inputs) != set(answers)
                or not training_ids or not holdout_ids
                or len(set(training_ids)) != len(training_ids)
                or len(set(holdout_ids)) != len(holdout_ids)
                or set(training_ids) & set(holdout_ids)
                or set(training_ids) | set(holdout_ids) != set(inputs)):
            raise ValueError("partition is invalid or leaks cases")
        for key, features in inputs.items():
            if (type(key) is not str or _ID.fullmatch(key) is None
                    or type(features) is not str or not re.fullmatch(r"[01]{1,16}", features)
                    or type(answers[key]) is not str or answers[key] not in _VALUE):
                raise ValueError("case input or label is invalid")
        self.case_id = case_id
        self._inputs = MappingProxyType(dict(inputs))
        self._answers = MappingProxyType(dict(answers))
        self._training = frozenset(training_ids)
        self._holdout = frozenset(holdout_ids)
        self._budget = probe_budget
        self._seen: set[str] = set()
        self._phase = "INQUIRY"
        self._score = None

    @property
    def probes_used(self) -> int:
        return len(self._seen)

    @property
    def closed(self) -> bool:
        return self._phase == "SCORED"

    def public_prompt(self) -> str:
        """No labels or held-out cases are in the subject's first message."""
        items = ", ".join(f"{key}:{self._inputs[key]}" for key in sorted(self._training))
        return (f"Synthetic binary rule inquiry {self.case_id}. "
                f"Choose up to {self._budget} distinct training case IDs for feedback. "
                f"Available unlabeled cases: {items}. "
                "After inquiry, predict withheld binary outcomes. "
                "There is no external tool or execution authority.")

    def probe(self, item: str) -> tuple[str, str]:
        if self._phase == "SCORED":
            raise TrialClosed("trial already scored")
        if self._phase != "INQUIRY":
            raise ProbeDenied("the inquiry phase has ended")
        if (type(item) is not str or item not in self._training
                or item in self._seen or self.probes_used >= self._budget):
            raise ProbeDenied("probe not allowed or budget exhausted")
        self._seen.add(item)
        return self._inputs[item], self._answers[item]

    def open_holdout(self) -> dict[str, str]:
        """End feedback, reveal *inputs only*, before the first prediction."""
        if self._phase != "INQUIRY":
            raise TrialClosed("holdout already opened or scored")
        self._phase = "PREDICTION"
        return {item: self._inputs[item] for item in sorted(self._holdout)}

    def commit_predictions(self, predictions: dict[str, str]) -> dict[str, object]:
        if self._phase == "SCORED":
            raise TrialClosed("predictions may be committed only once")
        if self._phase != "PREDICTION":
            raise ValueError("holdout must be opened after inquiry")
        if (type(predictions) is not dict
                or set(predictions) != self._holdout
                or any(type(v) is not str or v not in _VALUE
                       for v in predictions.values())):
            raise ValueError("exact, full, binary holdout prediction required")
        ordered = sorted(self._holdout)
        correct = sum(predictions[item] == self._answers[item] for item in ordered)
        digest = hashlib.sha256(json.dumps(
            {"trial": self.case_id, "predictions": {k: predictions[k] for k in ordered}},
            sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        self._phase = "SCORED"
        self._score = {
            "case_id": self.case_id,
            "correct": correct,
            "failed": len(ordered) - correct,
            "attempted": len(ordered),
            "probes_used": self.probes_used,
            "prediction_sha256": digest,
            "claim_ceiling": CLAIM_CEILING,
        }
        return dict(self._score)
