import hashlib
import json
from pathlib import Path

from runner.cli import main


ROOT = Path(__file__).resolve().parents[2]


def test_portfolio_wave_plan_cli_is_bounded_and_non_authorizing(capsys):
    code = main(
        [
            "portfolio-wave-plan",
            "--max-parallel",
            "5",
            "--max-per-identity",
            "1",
            "--max-per-family",
            "1",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "PORTFOLIO_WAVE_ADMISSION_PLAN_V1"
    assert payload["execution_authority"] is False
    assert payload["protected_effects_authorized"] is False
    wave_path = ROOT / "portfolio" / "advancement_wave.public.json"
    assert payload["wave_binding"]["sha256"] == hashlib.sha256(
        wave_path.read_bytes()
    ).hexdigest()
    assert payload["wave_binding"]["wave_id"] == "PROJECT_RUNNER_PORTFOLIO_ADVANCEMENT_WAVE_V1"
    assert payload["plan_binding"]["schema"] == (
        "PROJECT_RUNNER_PORTFOLIO_WAVE_PLAN_BINDING_V1"
    )
    canonical_payload = dict(payload)
    plan_binding = canonical_payload.pop("plan_binding")
    canonical = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert plan_binding["sha256"] == hashlib.sha256(canonical).hexdigest()
    assert payload["summary"]["selected"] <= 5
    assert max(payload["summary"]["selected_by_identity"].values()) <= 1
    assert max(payload["summary"]["selected_by_family"].values()) <= 1
    assert payload["selected"]
    first = payload["selected"][0]
    assert first["effect_ceiling"] == "SOURCE_ONLY"
    assert first["review_gate"]
    assert first["reviewer_identities"]
    assert first["frontier"]
    assert first["source_status"]


def test_portfolio_wave_plan_cli_respects_occupied_collision(capsys):
    code = main(
        [
            "portfolio-wave-plan",
            "--max-parallel",
            "10",
            "--max-per-identity",
            "2",
            "--max-per-family",
            "2",
            "--occupied-collision-key",
            "repository:thebrazenbeard/project-runner",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    selected = {item["subject_id"] for item in payload["selected"]}
    assert "project-runner" not in selected
    blocked = [
        item
        for item in payload["deferred"]
        if item["subject_id"] == "project-runner"
    ]
    assert blocked
    assert blocked[0]["reason"] == "COLLISION"
