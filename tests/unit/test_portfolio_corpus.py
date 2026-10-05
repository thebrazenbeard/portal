from pathlib import Path

import pytest
import yaml

from runner.portfolio_corpus import load_portfolio_corpus


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "corpus.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _base_payload() -> dict:
    return {
        "corpus_id": "PROJECT_RUNNER_PORTFOLIO_CORPUS_V1",
        "observed_at": "2026-09-24T13:08:00-04:00",
        "owner": "example",
        "counts": {
            "total": 2,
            "public": 1,
            "private": 1,
            "archived": 1,
            "public_archived": 0,
            "private_archived": 1,
        },
        "private_inventory": {
            "count": 1,
            "public_commitment_scheme": "COUNT_ONLY_PUBLIC_V1",
            "exact_membership_publicly_committed": False,
        },
        "workstream_counts": {"total": 2, "public": 1, "private": 1},
        "private_workstream_inventory": {
            "count": 1,
            "public_commitment_scheme": "COUNT_ONLY_PUBLIC_V1",
            "exact_membership_publicly_committed": False,
        },
        "workstreams": [
            {
                "id": "public-work",
                "name": "Public Work",
                "visibility": "public",
                "priority": "P1",
                "family_id": "research",
                "activity_state": "ACTIVE",
                "durable_surfaces": ["owner/public-one"],
                "purpose": "test workstream purpose",
                "status": "test workstream status",
            }
        ],
        "records": [
            {
                "id": "public-one",
                "repository": "owner/public-one",
                "name": "Public One",
                "visibility": "public",
                "archived": False,
                "default_branch": "main",
                "priority": "P0",
                "family_id": "spine",
                "activity_state": "ACTIVE",
                "purpose": "test purpose",
                "status": "test status",
            }
        ],
    }


def test_public_corpus_preserves_private_aggregates_without_identifiers(tmp_path: Path):
    corpus = load_portfolio_corpus(_write(tmp_path, _base_payload()), public_safe=True)
    assert corpus.counts.total == 2
    assert corpus.counts.public == 1
    assert corpus.counts.private == 1
    assert corpus.workstream_counts.total == 2
    assert corpus.workstream_counts.private == 1
    assert corpus.private_inventory_count == 1
    assert corpus.private_inventory_scheme == "COUNT_ONLY_PUBLIC_V1"
    assert corpus.private_inventory_exact_membership_publicly_committed is False
    assert tuple(record.repository for record in corpus.records) == ("owner/public-one",)
    assert tuple(item.id for item in corpus.workstreams) == ("public-work",)


def test_public_corpus_rejects_private_record(tmp_path: Path):
    payload = _base_payload()
    payload["records"].append(
        {
            "id": "private-one",
            "repository": "owner/private-one",
            "name": "Private One",
            "visibility": "private",
            "archived": True,
            "default_branch": "main",
            "priority": "P2",
            "family_id": "private-family",
            "activity_state": "ARCHIVED",
            "purpose": "private test purpose",
            "status": "private test status",
        }
    )
    with pytest.raises(ValueError, match="public-safe portfolio corpus contains private"):
        load_portfolio_corpus(_write(tmp_path, payload), public_safe=True)


def test_public_corpus_rejects_private_workstream(tmp_path: Path):
    payload = _base_payload()
    payload["workstreams"].append(
        {
            "id": "private-work",
            "name": "Private Work",
            "visibility": "private",
            "priority": "P3",
            "family_id": "private-family",
            "activity_state": "PAUSED",
            "durable_surfaces": [],
            "purpose": "private workstream purpose",
            "status": "private workstream status",
        }
    )
    with pytest.raises(ValueError, match="public-safe portfolio corpus contains private workstream"):
        load_portfolio_corpus(_write(tmp_path, payload), public_safe=True)


def test_public_corpus_requires_every_public_repository(tmp_path: Path):
    payload = _base_payload()
    payload["counts"].update(
        {
            "total": 2,
            "public": 2,
            "private": 0,
            "archived": 0,
            "public_archived": 0,
            "private_archived": 0,
        }
    )
    payload["private_inventory"]["count"] = 0
    with pytest.raises(ValueError, match="must enumerate every public repository"):
        load_portfolio_corpus(_write(tmp_path, payload), public_safe=True)


def test_duplicate_repository_is_rejected(tmp_path: Path):
    payload = _base_payload()
    duplicate = dict(payload["records"][0])
    duplicate["id"] = "public-two"
    payload["records"].append(duplicate)
    payload["counts"].update({"total": 3, "public": 2, "private": 1})
    with pytest.raises(ValueError, match="duplicate portfolio repository"):
        load_portfolio_corpus(_write(tmp_path, payload), public_safe=True)


def test_complete_corpus_requires_exact_visibility_and_archive_counts(tmp_path: Path):
    payload = _base_payload()
    payload["records"].append(
        {
            "id": "private-one",
            "repository": "owner/private-one",
            "name": "Private One",
            "visibility": "private",
            "archived": True,
            "default_branch": "main",
            "priority": "P4",
            "family_id": "archive",
            "activity_state": "ARCHIVED",
            "purpose": "private test purpose",
            "status": "private test status",
        }
    )
    payload["workstreams"].append(
        {
            "id": "private-work",
            "name": "Private Work",
            "visibility": "private",
            "priority": "P4",
            "family_id": "archive",
            "activity_state": "PAUSED",
            "durable_surfaces": [],
            "purpose": "private workstream purpose",
            "status": "private workstream status",
        }
    )
    corpus = load_portfolio_corpus(_write(tmp_path, payload), complete=True)
    assert len(corpus.records) == 2
    assert len(corpus.workstreams) == 2
    assert corpus.counts.archived == 1


def test_summary_is_aggregate_only(tmp_path: Path):
    corpus = load_portfolio_corpus(_write(tmp_path, _base_payload()), public_safe=True)
    summary = corpus.summary()
    assert summary["counts"] == {
        "total": 2,
        "public": 1,
        "private": 1,
        "archived": 1,
    }
    assert summary["workstream_counts"] == {
        "total": 2,
        "public": 1,
        "private": 1,
    }
    assert summary["priorities"] == {"P0": 1}
    assert summary["workstream_priorities"] == {"P1": 1}
    assert summary["published_records"] == 1
    assert "records" not in summary


def test_public_corpus_does_not_require_private_name_digest(tmp_path: Path):
    corpus = load_portfolio_corpus(_write(tmp_path, _base_payload()), public_safe=True)
    assert not hasattr(corpus, "private_inventory_sha256")
    assert not hasattr(corpus, "private_workstream_inventory_sha256")
