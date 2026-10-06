from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

from runner.portfolio_advancement import (
    load_advancement_wave,
    validate_wave_against_corpus,
)
from runner.portfolio_corpus import load_portfolio_corpus

from .discovery import RepositoryInventoryItem


_DISCOVERY_FRONTIER = (
    "Inspect current repository purpose, active work, dependencies, and authority "
    "before assigning effect-bearing work."
)
_DISCOVERY_PURPOSE = (
    "Live repository discovered after the baseline portfolio cut; semantic role "
    "has not yet been admitted into the curated corpus."
)
_DISCOVERY_STATUS = (
    "Live GitHub membership is current for this cut; descriptive purpose/frontier "
    "metadata still requires a bounded currentness audit."
)


@dataclass(frozen=True)
class PortalLivePortfolioRefresh:
    corpus_path: Path
    wave_path: Path
    public_repositories: int
    private_repositories: int
    archived_repositories: int


def _json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _live_index(
    owner: str,
    repositories: Iterable[RepositoryInventoryItem],
) -> dict[str, RepositoryInventoryItem]:
    index: dict[str, RepositoryInventoryItem] = {}
    for repo in repositories:
        observed_owner, _, _ = repo.full_name.partition("/")
        if observed_owner.casefold() != owner.casefold():
            raise ValueError(
                "live repository is outside baseline portfolio owner"
            )
        key = repo.full_name.casefold()
        if key in index:
            raise ValueError(f"duplicate live repository: {repo.full_name}")
        index[key] = repo
    return index


def _discovery_record(repo: RepositoryInventoryItem) -> dict[str, object]:
    archived = bool(repo.archived)
    return {
        "id": repo.name.casefold(),
        "repository": repo.full_name,
        "name": repo.name,
        "visibility": "public",
        "archived": archived,
        "default_branch": repo.default_branch,
        "priority": "P3",
        "family_id": "live-discovery",
        "activity_state": "ARCHIVED" if archived else "UNKNOWN",
        "purpose": _DISCOVERY_PURPOSE,
        "status": (
            "Live GitHub membership marks this repository archived; no new work "
            "is admitted."
            if archived
            else _DISCOVERY_STATUS
        ),
        "current_frontier": (
            "Preserve archived repository; do not admit new work."
            if archived
            else _DISCOVERY_FRONTIER
        ),
    }


def _refresh_known_record(
    raw: dict[str, object],
    repo: RepositoryInventoryItem,
) -> dict[str, object]:
    record = dict(raw)
    record["visibility"] = "public"
    record["archived"] = bool(repo.archived)
    record["default_branch"] = repo.default_branch
    if repo.archived:
        record["activity_state"] = "ARCHIVED"
        record["status"] = (
            "Live GitHub membership marks this repository archived; prior "
            "descriptive metadata is retained only as historical context."
        )
        record["current_frontier"] = (
            "Preserve archived repository; do not admit new work."
        )
    return record


def _refresh_wave_item(
    *,
    record: dict[str, object],
    baseline_item: dict[str, object] | None,
) -> dict[str, object]:
    archived = bool(record["archived"])
    if baseline_item is None:
        item: dict[str, object] = {
            "subject_kind": "repository",
            "subject_id": record["id"],
            "repository": record["repository"],
            "priority": record["priority"],
            "family_id": record["family_id"],
            "activity_state": record["activity_state"],
            "lead_identity": "DISCOVERY",
            "reviewer_identities": ["REZON"],
            "action": "PRESERVE_ONLY" if archived else "CURRENTNESS_AUDIT",
            "execution_state": "HELD" if archived else "QUEUED",
            "effect_ceiling": "NO_EFFECT",
            "review_gate": "NO_NEW_WORK" if archived else "CURRENTNESS_ONLY",
            "frontier": record["current_frontier"],
            "source_status": record["status"],
        }
        return item

    item = dict(baseline_item)
    item["subject_id"] = record["id"]
    item["repository"] = record["repository"]
    item["priority"] = record["priority"]
    item["family_id"] = record["family_id"]
    item["activity_state"] = record["activity_state"]
    item["frontier"] = record["current_frontier"]
    item["source_status"] = record["status"]
    if archived:
        item["action"] = "PRESERVE_ONLY"
        item["execution_state"] = "HELD"
        item["effect_ceiling"] = "NO_EFFECT"
        item["review_gate"] = "NO_NEW_WORK"
    return item


def refresh_live_public_portfolio(
    *,
    baseline_corpus_path: Path,
    baseline_wave_path: Path,
    repositories: Iterable[RepositoryInventoryItem],
    observed_at: str,
    output_dir: Path,
) -> PortalLivePortfolioRefresh:
    """Render a privacy-safe live public corpus/wave overlay.

    Exact live membership replaces stale membership. Existing descriptive
    metadata is reused only for repositories that still have the same exact
    repository identity. Newly discovered public repositories receive a
    NO_EFFECT currentness-audit frontier instead of invented project authority.
    Private membership is represented only by counts.
    """

    observed_at = observed_at.strip()
    if not observed_at:
        raise ValueError("observed_at is required")

    baseline_corpus = load_portfolio_corpus(
        Path(baseline_corpus_path),
        public_safe=True,
    )
    baseline_wave = load_advancement_wave(Path(baseline_wave_path))
    validate_wave_against_corpus(
        baseline_wave,
        baseline_corpus,
        public_safe=True,
    )
    if "DISCOVERY" not in baseline_wave.identities or "REZON" not in baseline_wave.identities:
        raise ValueError(
            "baseline wave must define DISCOVERY and REZON identities"
        )

    live = _live_index(baseline_corpus.owner, repositories)
    public_live = tuple(
        sorted(
            (repo for repo in live.values() if not repo.private),
            key=lambda repo: repo.full_name.casefold(),
        )
    )
    private_live = tuple(repo for repo in live.values() if repo.private)

    corpus_raw = json.loads(Path(baseline_corpus_path).read_text(encoding="utf-8"))
    wave_raw = json.loads(Path(baseline_wave_path).read_text(encoding="utf-8"))

    baseline_records = {
        str(raw["repository"]).casefold(): raw
        for raw in corpus_raw["records"]
    }
    baseline_items = {
        str(raw["repository"]).casefold(): raw
        for raw in wave_raw["items"]
        if raw.get("subject_kind") == "repository"
    }

    records: list[dict[str, object]] = []
    repository_items: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for repo in public_live:
        key = repo.full_name.casefold()
        raw_record = baseline_records.get(key)
        if raw_record is None:
            record = _discovery_record(repo)
        else:
            record = _refresh_known_record(raw_record, repo)

        record_id = str(record["id"])
        if record_id in seen_ids:
            raise ValueError(
                f"live public repository id collision: {record_id}"
            )
        seen_ids.add(record_id)
        records.append(record)
        repository_items.append(
            _refresh_wave_item(
                record=record,
                baseline_item=baseline_items.get(key),
            )
        )

    public_archived = sum(repo.archived for repo in public_live)
    private_archived = sum(repo.archived for repo in private_live)
    counts = {
        "total": len(live),
        "public": len(public_live),
        "private": len(private_live),
        "archived": public_archived + private_archived,
        "public_archived": public_archived,
        "private_archived": private_archived,
    }

    corpus_payload = dict(corpus_raw)
    corpus_payload["observed_at"] = observed_at
    corpus_payload["status_basis"] = (
        "Live GitHub owner membership overlaid onto the previous public-safe "
        "curated corpus. Exact private membership is intentionally not published."
    )
    corpus_payload["counts"] = counts
    corpus_payload["private_inventory"] = {
        "count": len(private_live),
        "public_commitment_scheme": "COUNT_ONLY_PUBLIC_V1",
        "exact_membership_publicly_committed": False,
    }
    corpus_payload["records"] = sorted(records, key=lambda raw: str(raw["id"]))

    output_dir = Path(output_dir)
    corpus_path = output_dir / "corpus.live.public.json"
    wave_path = output_dir / "advancement_wave.live.public.json"
    _json_write(corpus_path, corpus_payload)

    live_corpus = load_portfolio_corpus(corpus_path, public_safe=True)
    workstream_items = [
        raw
        for raw in wave_raw["items"]
        if raw.get("subject_kind") == "workstream"
    ]
    wave_payload = dict(wave_raw)
    wave_payload["generated_at"] = observed_at
    wave_payload["corpus_binding"] = {
        "binding_kind": "LOCAL_SHA256",
        "sha256": live_corpus.sha256,
        "public_repository_count": counts["public"],
        "total_repository_count": counts["total"],
        "private_repository_count": counts["private"],
        "public_workstream_count": live_corpus.workstream_counts.public,
        "total_workstream_count": live_corpus.workstream_counts.total,
        "private_workstream_count": live_corpus.workstream_counts.private,
    }
    wave_payload["items"] = sorted(
        [*repository_items, *workstream_items],
        key=lambda raw: (str(raw["subject_kind"]), str(raw["subject_id"])),
    )
    _json_write(wave_path, wave_payload)

    live_wave = load_advancement_wave(wave_path)
    validate_wave_against_corpus(live_wave, live_corpus, public_safe=True)
    return PortalLivePortfolioRefresh(
        corpus_path=corpus_path,
        wave_path=wave_path,
        public_repositories=counts["public"],
        private_repositories=counts["private"],
        archived_repositories=counts["archived"],
    )
