from pathlib import Path
import json

import pytest

from runner.portfolio_advancement import (
    load_advancement_wave,
    validate_wave_against_corpus,
)
from runner.portfolio_corpus import load_portfolio_corpus


ROOT = Path(__file__).resolve().parents[2]


def test_public_wave_covers_public_corpus_exactly():
    corpus = load_portfolio_corpus(
        ROOT / "portfolio" / "corpus.public.json",
        public_safe=True,
    )
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    validate_wave_against_corpus(wave, corpus, public_safe=True)

    repo_items = [
        item for item in wave.items if item.subject_kind == "repository"
    ]
    workstream_items = [
        item for item in wave.items if item.subject_kind == "workstream"
    ]
    assert len(repo_items) == 53
    assert len(workstream_items) == 2


def test_every_queued_subject_has_independent_review():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    for item in wave.items:
        if item.execution_state != "QUEUED":
            continue
        assert item.reviewer_identities
        assert item.lead_identity not in item.reviewer_identities


def test_effect_ceiling_is_source_only_or_lower():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    assert {item.effect_ceiling for item in wave.items} <= {
        "SOURCE_ONLY",
        "NO_EFFECT",
    }


def test_superseded_subject_is_preserve_only_and_held():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    build_team = next(
        item for item in wave.items if item.subject_id == "build-team-2.0"
    )
    assert build_team.activity_state == "SUPERSEDED"
    assert build_team.action == "PRESERVE_ONLY"
    assert build_team.execution_state == "HELD"
    assert build_team.effect_ceiling == "NO_EFFECT"


def test_wave_rejects_self_review(tmp_path: Path):
    source = json.loads(
        (ROOT / "portfolio" / "advancement_wave.public.json").read_text(
            encoding="utf-8"
        )
    )
    source["items"][0]["reviewer_identities"] = [
        source["items"][0]["lead_identity"]
    ]
    path = tmp_path / "wave.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot review itself"):
        load_advancement_wave(path)


def test_wave_rejects_membership_drift(tmp_path: Path):
    corpus = load_portfolio_corpus(
        ROOT / "portfolio" / "corpus.public.json",
        public_safe=True,
    )
    source = json.loads(
        (ROOT / "portfolio" / "advancement_wave.public.json").read_text(
            encoding="utf-8"
        )
    )
    source["items"] = source["items"][1:]
    path = tmp_path / "wave.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    wave = load_advancement_wave(path)
    with pytest.raises(ValueError, match="repository membership"):
        validate_wave_against_corpus(wave, corpus, public_safe=True)


def test_summary_and_identity_projection_are_deterministic():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    summary = wave.summary()
    assert summary["subjects"] == 55
    assert summary["held"] >= 1
    one_items = wave.for_identity("ONE")
    assert all(item.lead_identity == "ONE" for item in one_items)
    assert all(item.execution_state == "QUEUED" for item in one_items)


def test_sql_connectome_is_admitted_without_effect_authority():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    item = next(
        item for item in wave.items
        if item.subject_kind == "repository"
        and item.subject_id == "sql-connectome"
    )
    assert item.repositories == ("thebrazenbeard/sql-connectome",)
    assert item.effect_ceiling == "SOURCE_ONLY"
    assert item.execution_state == "QUEUED"
    assert item.priority == "P1"


def test_20260928_new_public_repositories_are_source_only():
    wave = load_advancement_wave(
        ROOT / "portfolio" / "advancement_wave.public.json"
    )
    expected = {"axle", "ingest", "lgcm", "pro-run"}
    items = {
        item.subject_id: item
        for item in wave.items
        if item.subject_kind == "repository"
        and item.subject_id in expected
    }
    assert set(items) == expected
    assert all(item.effect_ceiling == "SOURCE_ONLY" for item in items.values())
    assert all(item.execution_state == "QUEUED" for item in items.values())
    assert all(item.review_gate == "EXACT_HEAD_REVIEW" for item in items.values())
