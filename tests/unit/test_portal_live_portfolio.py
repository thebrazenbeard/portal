from __future__ import annotations

import json
from pathlib import Path

from portal.discovery import RepositoryInventoryItem
from portal.live_portfolio import refresh_live_public_portfolio
from runner.portfolio_advancement import (
    load_advancement_wave,
    validate_wave_against_corpus,
)
from runner.portfolio_corpus import load_portfolio_corpus


def _repo(
    name: str,
    *,
    private: bool = False,
    archived: bool = False,
) -> RepositoryInventoryItem:
    return RepositoryInventoryItem(
        name=name,
        full_name=f"thebrazenbeard/{name}",
        private=private,
        archived=archived,
        default_branch="main",
    )


def _baseline(tmp_path: Path) -> tuple[Path, Path]:
    corpus = {
        "corpus_id": "PROJECT_RUNNER_PORTFOLIO_CORPUS_V1",
        "observed_at": "2026-09-28T00:00:00Z",
        "owner": "thebrazenbeard",
        "status_basis": "historical fixture",
        "freshness_rule": "refresh before current use",
        "counts": {
            "total": 3,
            "public": 2,
            "private": 1,
            "archived": 0,
            "public_archived": 0,
            "private_archived": 0,
        },
        "private_inventory": {
            "count": 1,
            "public_commitment_scheme": "COUNT_ONLY_PUBLIC_V1",
            "exact_membership_publicly_committed": False,
        },
        "workstream_counts": {"total": 0, "public": 0, "private": 0},
        "private_workstream_inventory": {
            "count": 0,
            "public_commitment_scheme": "COUNT_ONLY_PUBLIC_V1",
            "exact_membership_publicly_committed": False,
        },
        "workstreams": [],
        "records": [
            {
                "id": "alpha",
                "repository": "thebrazenbeard/alpha",
                "name": "Alpha",
                "visibility": "public",
                "archived": False,
                "default_branch": "main",
                "priority": "P1",
                "family_id": "known",
                "activity_state": "ACTIVE",
                "purpose": "Known current project.",
                "status": "Known status.",
                "current_frontier": "Known frontier.",
            },
            {
                "id": "gone",
                "repository": "thebrazenbeard/gone",
                "name": "Gone",
                "visibility": "public",
                "archived": False,
                "default_branch": "main",
                "priority": "P2",
                "family_id": "known",
                "activity_state": "STABLE",
                "purpose": "Old project.",
                "status": "Historical membership.",
                "current_frontier": "Refresh if it returns.",
            },
        ],
    }
    corpus_path = tmp_path / "baseline-corpus.json"
    corpus_path.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")

    wave = {
        "wave_id": "PROJECT_RUNNER_PORTFOLIO_ADVANCEMENT_WAVE_V1",
        "generated_at": "2026-09-28T00:00:00Z",
        "corpus_binding": {
            "repository": "thebrazenbeard/project-runner",
            "ref": "historical",
            "commit": "a" * 40,
            "path": "portfolio/corpus.public.json",
            "git_blob_sha": "b" * 40,
            "public_repository_count": 2,
            "total_repository_count": 3,
            "private_repository_count": 1,
            "public_workstream_count": 0,
            "total_workstream_count": 0,
            "private_workstream_count": 0,
        },
        "identities": {
            "DISCOVERY": "portfolio discovery/currentness",
            "REZON": "independent reviewer",
        },
        "policy": {
            "protected_effects_forbidden_without_separate_live_authority": True,
            "merge_forbidden": True,
            "deploy_forbidden": True,
            "credential_or_permission_changes_forbidden": True,
            "destructive_cleanup_forbidden": True,
            "default_effect_ceiling": "SOURCE_ONLY",
            "priority_is_authority": False,
            "execution_rule": "Every live subject receives a bounded disposition.",
        },
        "items": [
            {
                "subject_kind": "repository",
                "subject_id": "alpha",
                "repository": "thebrazenbeard/alpha",
                "priority": "P1",
                "family_id": "known",
                "activity_state": "ACTIVE",
                "lead_identity": "DISCOVERY",
                "reviewer_identities": ["REZON"],
                "action": "EXECUTE_FRONTIER",
                "execution_state": "QUEUED",
                "effect_ceiling": "SOURCE_ONLY",
                "review_gate": "EXACT_HEAD_REVIEW",
                "frontier": "Known frontier.",
                "source_status": "Known status.",
            },
            {
                "subject_kind": "repository",
                "subject_id": "gone",
                "repository": "thebrazenbeard/gone",
                "priority": "P2",
                "family_id": "known",
                "activity_state": "STABLE",
                "lead_identity": "DISCOVERY",
                "reviewer_identities": ["REZON"],
                "action": "VERIFY_REUSE_OR_HOLD",
                "execution_state": "QUEUED",
                "effect_ceiling": "SOURCE_ONLY",
                "review_gate": "CURRENTNESS",
                "frontier": "Refresh if it returns.",
                "source_status": "Historical membership.",
            },
        ],
    }
    wave_path = tmp_path / "baseline-wave.json"
    wave_path.write_text(json.dumps(wave, indent=2) + "\n", encoding="utf-8")
    return corpus_path, wave_path


def test_live_overlay_covers_current_public_membership_without_leaking_private_names(
    tmp_path: Path,
) -> None:
    corpus_path, wave_path = _baseline(tmp_path)
    result = refresh_live_public_portfolio(
        baseline_corpus_path=corpus_path,
        baseline_wave_path=wave_path,
        repositories=(
            _repo("alpha"),
            _repo("beta"),
            _repo("archived", archived=True),
            _repo("secret", private=True),
        ),
        observed_at="2026-10-06T11:30:00Z",
        output_dir=tmp_path / "live",
    )

    corpus = load_portfolio_corpus(result.corpus_path, public_safe=True)
    assert corpus.counts.total == 4
    assert corpus.counts.public == 3
    assert corpus.counts.private == 1
    assert corpus.counts.archived == 1
    assert corpus.counts.public_archived == 1
    assert corpus.counts.private_archived == 0
    assert {record.repository for record in corpus.records} == {
        "thebrazenbeard/alpha",
        "thebrazenbeard/beta",
        "thebrazenbeard/archived",
    }

    by_repo = {record.repository: record for record in corpus.records}
    assert by_repo["thebrazenbeard/alpha"].priority.value == "P1"
    assert by_repo["thebrazenbeard/alpha"].current_frontier == "Known frontier."
    assert by_repo["thebrazenbeard/beta"].priority.value == "P3"
    assert by_repo["thebrazenbeard/beta"].activity_state.value == "UNKNOWN"
    assert by_repo["thebrazenbeard/archived"].activity_state.value == "ARCHIVED"

    raw_corpus = result.corpus_path.read_text(encoding="utf-8")
    raw_wave = result.wave_path.read_text(encoding="utf-8")
    assert "secret" not in raw_corpus
    assert "secret" not in raw_wave
    assert "gone" not in raw_corpus
    assert "gone" not in raw_wave


def test_live_overlay_wave_uses_exact_local_corpus_digest_and_safe_discovery_work(
    tmp_path: Path,
) -> None:
    corpus_path, wave_path = _baseline(tmp_path)
    result = refresh_live_public_portfolio(
        baseline_corpus_path=corpus_path,
        baseline_wave_path=wave_path,
        repositories=(
            _repo("alpha"),
            _repo("beta"),
            _repo("archived", archived=True),
        ),
        observed_at="2026-10-06T11:30:00Z",
        output_dir=tmp_path / "live",
    )

    corpus = load_portfolio_corpus(result.corpus_path, public_safe=True)
    wave = load_advancement_wave(result.wave_path)
    validate_wave_against_corpus(wave, corpus, public_safe=True)

    assert wave.corpus_binding["binding_kind"] == "LOCAL_SHA256"
    assert wave.corpus_binding["sha256"] == corpus.sha256

    items = {
        item.repositories[0]: item
        for item in wave.items
        if item.subject_kind == "repository"
    }
    beta = items["thebrazenbeard/beta"]
    assert beta.action == "CURRENTNESS_AUDIT"
    assert beta.execution_state == "QUEUED"
    assert beta.effect_ceiling == "NO_EFFECT"
    assert beta.lead_identity == "DISCOVERY"
    assert beta.reviewer_identities == ("REZON",)

    archived = items["thebrazenbeard/archived"]
    assert archived.action == "PRESERVE_ONLY"
    assert archived.execution_state == "HELD"
    assert archived.effect_ceiling == "NO_EFFECT"


def test_live_overlay_rejects_missing_discovery_identities(tmp_path: Path) -> None:
    corpus_path, wave_path = _baseline(tmp_path)
    payload = json.loads(wave_path.read_text(encoding="utf-8"))
    payload["identities"].pop("DISCOVERY")
    payload["identities"]["OTHER"] = "schema-valid alternate identity"
    for item in payload["items"]:
        if item["lead_identity"] == "DISCOVERY":
            item["lead_identity"] = "OTHER"
    wave_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="DISCOVERY and REZON"):
        refresh_live_public_portfolio(
            baseline_corpus_path=corpus_path,
            baseline_wave_path=wave_path,
            repositories=(_repo("beta"),),
            observed_at="2026-10-06T11:30:00Z",
            output_dir=tmp_path / "live",
        )
