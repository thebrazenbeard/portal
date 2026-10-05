from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .portfolio_advancement import load_advancement_wave
from .portfolio_wave_scheduler import (
    WaveExecutionBudget,
    plan_wave_admission,
)


def build_bound_wave_plan_payload(
    wave_path: Path,
    *,
    budget: WaveExecutionBudget,
    occupied_collision_keys: Iterable[str] = (),
) -> dict[str, object]:
    """Build the canonical digest-bound Project Runner admission plan payload."""

    wave_path = Path(wave_path)
    wave_bytes = wave_path.read_bytes()
    wave_sha256 = hashlib.sha256(wave_bytes).hexdigest()
    wave = load_advancement_wave(wave_path)
    plan = plan_wave_admission(
        wave,
        budget=budget,
        occupied_collision_keys=occupied_collision_keys,
    )
    payload: dict[str, object] = {
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
    return payload


def write_bound_wave_plan(
    path: Path,
    payload: dict[str, object],
) -> None:
    """Persist exact plan evidence for later durable claim verification."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
