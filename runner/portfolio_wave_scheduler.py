from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable

from .portfolio_advancement import AdvancementItem, AdvancementWave


_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
_INERT_ACTIONS = {"PRESERVE_ONLY", "REFRESH_IF_REACTIVATED"}


@dataclass(frozen=True)
class WaveExecutionBudget:
    max_parallel: int
    max_per_identity: int
    max_per_family: int
    max_per_lane: int | None = None

    def validate(self) -> None:
        if type(self.max_parallel) is not int or self.max_parallel < 1:
            raise ValueError("max_parallel must be a positive integer")
        if type(self.max_per_identity) is not int or self.max_per_identity < 1:
            raise ValueError("max_per_identity must be a positive integer")
        if type(self.max_per_family) is not int or self.max_per_family < 1:
            raise ValueError("max_per_family must be a positive integer")
        if self.max_per_identity > self.max_parallel:
            raise ValueError("max_per_identity cannot exceed max_parallel")
        if self.max_per_family > self.max_parallel:
            raise ValueError("max_per_family cannot exceed max_parallel")
        if self.max_per_lane is not None:
            if type(self.max_per_lane) is not int or self.max_per_lane < 1:
                raise ValueError("max_per_lane must be a positive integer")
            if self.max_per_lane > self.max_parallel:
                raise ValueError("max_per_lane cannot exceed max_parallel")


@dataclass(frozen=True)
class WaveAdmission:
    subject_kind: str
    subject_id: str
    family_id: str
    lead_identity: str
    lane_id: str
    reviewer_identities: tuple[str, ...]
    priority: str
    action: str
    activity_state: str
    effect_ceiling: str
    review_gate: str
    frontier: str | None
    source_status: str
    collision_keys: tuple[str, ...]


@dataclass(frozen=True)
class WaveDeferral:
    subject_kind: str
    subject_id: str
    family_id: str
    lead_identity: str
    lane_id: str
    priority: str
    reason: str
    collision_keys: tuple[str, ...]


@dataclass(frozen=True)
class WaveAdmissionPlan:
    selected: tuple[WaveAdmission, ...]
    deferred: tuple[WaveDeferral, ...]
    budget: WaveExecutionBudget
    occupied_collision_keys: tuple[str, ...]
    excluded_subjects: tuple[tuple[str, str], ...] = ()

    def summary(self) -> dict[str, object]:
        return {
            "selected": len(self.selected),
            "deferred": len(self.deferred),
            "max_parallel": self.budget.max_parallel,
            "max_per_identity": self.budget.max_per_identity,
            "max_per_family": self.budget.max_per_family,
            "max_per_lane": self.budget.max_per_lane,
            "selected_by_identity": dict(sorted(Counter(
                item.lead_identity for item in self.selected
            ).items())),
            "selected_by_priority": dict(sorted(Counter(
                item.priority for item in self.selected
            ).items())),
            "selected_by_lane": dict(sorted(Counter(
                item.lane_id for item in self.selected
            ).items())),
            "selected_by_family": dict(sorted(Counter(
                item.family_id for item in self.selected
            ).items())),
            "deferred_by_reason": dict(sorted(Counter(
                item.reason for item in self.deferred
            ).items())),
            "occupied_collision_keys": list(self.occupied_collision_keys),
            "excluded_subjects": [
                f"{kind}:{subject_id}"
                for kind, subject_id in self.excluded_subjects
            ],
        }


def collision_keys(item: AdvancementItem) -> tuple[str, ...]:
    keys: set[str] = set()
    for surface in item.repositories:
        value = surface.strip()
        if not value:
            continue
        if _REPOSITORY.fullmatch(value):
            keys.add(f"repository:{value.lower()}")
        else:
            keys.add(f"surface:{value.lower()}")
    if not keys:
        keys.add(f"{item.subject_kind}:{item.subject_id}")
    return tuple(sorted(keys))


def _admission_sort_key(item: AdvancementItem) -> tuple[object, ...]:
    return (
        _PRIORITY_ORDER.get(item.priority, 99),
        item.lead_identity,
        item.subject_kind,
        item.subject_id,
    )


def plan_wave_admission(
    wave: AdvancementWave,
    *,
    budget: WaveExecutionBudget,
    occupied_collision_keys: Iterable[str] = (),
    excluded_subjects: Iterable[tuple[str, str]] = (),
) -> WaveAdmissionPlan:
    """Select a deterministic, collision-free source-work slice.

    Admission is scheduling evidence only. It grants no repository/provider
    authority and does not perform execution, merge, deployment, installation,
    credential mutation, or any other protected effect.
    """
    budget.validate()
    occupied = {str(value).strip().lower() for value in occupied_collision_keys}
    if "" in occupied:
        raise ValueError("occupied collision keys must be non-empty strings")

    excluded: set[tuple[str, str]] = set()
    for value in excluded_subjects:
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or not all(isinstance(part, str) and part.strip() for part in value)
        ):
            raise ValueError(
                "excluded subjects must be (subject_kind, subject_id) string pairs"
            )
        excluded.add((value[0].strip(), value[1].strip()))

    selected: list[WaveAdmission] = []
    deferred: list[WaveDeferral] = []
    reserved = set(occupied)
    identity_load: Counter[str] = Counter()
    family_load: Counter[str] = Counter()
    lane_load: Counter[str] = Counter()

    for item in sorted(wave.items, key=_admission_sort_key):
        keys = collision_keys(item)
        lane_id = item.effective_lane

        if (item.subject_kind, item.subject_id) in excluded:
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="EXCLUDED_TERMINAL",
                collision_keys=keys,
            ))
            continue

        if item.execution_state != "QUEUED":
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="NOT_QUEUED",
                collision_keys=keys,
            ))
            continue

        if item.action in _INERT_ACTIONS:
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="INERT_ACTION",
                collision_keys=keys,
            ))
            continue

        if item.effect_ceiling not in {"SOURCE_ONLY", "NO_EFFECT"}:
            raise ValueError(
                f"wave item exceeds source-only admission ceiling: {item.subject_id}"
            )

        if reserved.intersection(keys):
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="COLLISION",
                collision_keys=keys,
            ))
            continue

        if len(selected) >= budget.max_parallel:
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="GLOBAL_BUDGET",
                collision_keys=keys,
            ))
            continue

        if (
            budget.max_per_lane is not None
            and lane_load[lane_id] >= budget.max_per_lane
        ):
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="LANE_BUDGET",
                collision_keys=keys,
            ))
            continue

        if identity_load[item.lead_identity] >= budget.max_per_identity:
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="IDENTITY_BUDGET",
                collision_keys=keys,
            ))
            continue

        if family_load[item.family_id] >= budget.max_per_family:
            deferred.append(WaveDeferral(
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
                family_id=item.family_id,
                lead_identity=item.lead_identity,
                lane_id=lane_id,
                priority=item.priority,
                reason="FAMILY_BUDGET",
                collision_keys=keys,
            ))
            continue

        selected.append(WaveAdmission(
            subject_kind=item.subject_kind,
            subject_id=item.subject_id,
            family_id=item.family_id,
            lead_identity=item.lead_identity,
            lane_id=lane_id,
            reviewer_identities=item.reviewer_identities,
            priority=item.priority,
            action=item.action,
            activity_state=item.activity_state,
            effect_ceiling=item.effect_ceiling,
            review_gate=item.review_gate,
            frontier=item.frontier,
            source_status=item.source_status,
            collision_keys=keys,
        ))
        identity_load[item.lead_identity] += 1
        family_load[item.family_id] += 1
        lane_load[lane_id] += 1
        reserved.update(keys)

    return WaveAdmissionPlan(
        selected=tuple(selected),
        deferred=tuple(deferred),
        budget=budget,
        occupied_collision_keys=tuple(sorted(occupied)),
        excluded_subjects=tuple(sorted(excluded)),
    )
