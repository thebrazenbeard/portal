from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from .dedup import frontier_fingerprint
from .models import Frontier, FrontierStatus


_DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policy" / "scheduling.yaml"


@dataclass(frozen=True)
class PriorityDecision:
    frontier: Frontier
    score: int
    reasons: tuple[str, ...]


def _load_default_weights() -> dict[str, int]:
    payload = yaml.safe_load(_DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    raw = payload.get("weights", {})
    return {str(key): int(value) for key, value in raw.items()}


def _weights(policy: Mapping[str, object] | None) -> dict[str, int]:
    if policy is None:
        return _load_default_weights()
    raw = policy.get("weights", policy)
    if not isinstance(raw, Mapping):
        raise ValueError("scheduling policy weights must be a mapping")
    return {str(key): int(value) for key, value in raw.items()}


def _is_executable(frontier: Frontier) -> bool:
    return frontier.status is FrontierStatus.READY


def rank_frontiers(
    frontiers: Iterable[Frontier],
    policy: Mapping[str, object] | None = None,
) -> tuple[PriorityDecision, ...]:
    weights = _weights(policy)
    decisions: list[PriorityDecision] = []

    for frontier in frontiers:
        score = 0
        reasons: list[str] = [
            "class=executable" if _is_executable(frontier) else f"class=blocked:{frontier.status.value}"
        ]
        for factor in sorted(frontier.priority_inputs):
            value = int(frontier.priority_inputs[factor])
            weight = int(weights.get(factor, 0))
            contribution = value * weight
            score += contribution
            if value != 0:
                reasons.append(
                    f"{factor}={value} weight={weight} contribution={contribution}"
                )
        decisions.append(PriorityDecision(frontier=frontier, score=score, reasons=tuple(reasons)))

    decisions.sort(
        key=lambda item: (
            0 if _is_executable(item.frontier) else 1,
            -item.score,
            frontier_fingerprint(item.frontier),
        )
    )
    return tuple(decisions)
