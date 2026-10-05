from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
from typing import Mapping

import yaml

from .schema import validate_document


class PortfolioPriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class PortfolioActivityState(str, Enum):
    ACTIVE_HEAVY = "ACTIVE_HEAVY"
    ACTIVE = "ACTIVE"
    STABLE = "STABLE"
    INCUBATOR = "INCUBATOR"
    QUIET = "QUIET"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True)
class PortfolioCounts:
    total: int
    public: int
    private: int
    archived: int
    public_archived: int
    private_archived: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "PortfolioCounts":
        return cls(**{
            key: int(data[key])
            for key in (
                "total", "public", "private",
                "archived", "public_archived", "private_archived"
            )
        })

    def validate(self) -> None:
        if self.total != self.public + self.private:
            raise ValueError("portfolio count mismatch: total != public + private")
        if self.archived != self.public_archived + self.private_archived:
            raise ValueError("portfolio count mismatch: archived split does not sum")
        if self.public_archived > self.public or self.private_archived > self.private:
            raise ValueError("portfolio archived count exceeds visibility count")


@dataclass(frozen=True)
class WorkstreamCounts:
    total: int
    public: int
    private: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "WorkstreamCounts":
        return cls(
            total=int(data["total"]),
            public=int(data["public"]),
            private=int(data["private"]),
        )

    def validate(self) -> None:
        if self.total != self.public + self.private:
            raise ValueError("workstream count mismatch: total != public + private")


@dataclass(frozen=True)
class PortfolioRecord:
    id: str
    repository: str
    name: str
    visibility: str
    archived: bool
    default_branch: str
    priority: PortfolioPriority
    family_id: str
    activity_state: PortfolioActivityState
    purpose: str
    status: str
    current_frontier: str | None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "PortfolioRecord":
        return cls(
            id=str(data["id"]),
            repository=str(data["repository"]),
            name=str(data["name"]),
            visibility=str(data["visibility"]),
            archived=bool(data["archived"]),
            default_branch=str(data["default_branch"]),
            priority=PortfolioPriority(str(data["priority"])),
            family_id=str(data["family_id"]),
            activity_state=PortfolioActivityState(str(data["activity_state"])),
            purpose=str(data["purpose"]),
            status=str(data["status"]),
            current_frontier=(
                str(data["current_frontier"])
                if data.get("current_frontier") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class WorkstreamRecord:
    id: str
    name: str
    visibility: str
    priority: PortfolioPriority
    family_id: str
    activity_state: PortfolioActivityState
    durable_surfaces: tuple[str, ...]
    purpose: str
    status: str
    current_frontier: str | None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "WorkstreamRecord":
        raw_surfaces = data.get("durable_surfaces", [])
        if not isinstance(raw_surfaces, list):
            raise ValueError("durable_surfaces must be a list")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            visibility=str(data["visibility"]),
            priority=PortfolioPriority(str(data["priority"])),
            family_id=str(data["family_id"]),
            activity_state=PortfolioActivityState(str(data["activity_state"])),
            durable_surfaces=tuple(str(item) for item in raw_surfaces),
            purpose=str(data["purpose"]),
            status=str(data["status"]),
            current_frontier=(
                str(data["current_frontier"])
                if data.get("current_frontier") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PortfolioCorpusSnapshot:
    corpus_id: str
    observed_at: str
    owner: str
    status_basis: str | None
    freshness_rule: str | None
    counts: PortfolioCounts
    workstream_counts: WorkstreamCounts
    private_inventory_count: int
    private_inventory_scheme: str
    private_inventory_exact_membership_publicly_committed: bool
    private_workstream_inventory_count: int
    private_workstream_inventory_scheme: str
    private_workstream_exact_membership_publicly_committed: bool
    records: tuple[PortfolioRecord, ...]
    workstreams: tuple[WorkstreamRecord, ...]
    sha256: str
    byte_length: int

    def summary(self) -> dict[str, object]:
        priorities = Counter(record.priority.value for record in self.records)
        activities = Counter(record.activity_state.value for record in self.records)
        families = Counter(record.family_id for record in self.records)
        workstream_priorities = Counter(
            item.priority.value for item in self.workstreams
        )
        workstream_activities = Counter(
            item.activity_state.value for item in self.workstreams
        )
        return {
            "corpus_id": self.corpus_id,
            "observed_at": self.observed_at,
            "counts": {
                "total": self.counts.total,
                "public": self.counts.public,
                "private": self.counts.private,
                "archived": self.counts.archived,
            },
            "workstream_counts": {
                "total": self.workstream_counts.total,
                "public": self.workstream_counts.public,
                "private": self.workstream_counts.private,
            },
            "published_records": len(self.records),
            "published_workstreams": len(self.workstreams),
            "priorities": dict(sorted(priorities.items())),
            "activities": dict(sorted(activities.items())),
            "families": dict(sorted(families.items())),
            "workstream_priorities": dict(sorted(workstream_priorities.items())),
            "workstream_activities": dict(sorted(workstream_activities.items())),
            "sha256": self.sha256,
        }


def _reject_duplicate(records: tuple[object, ...], attr: str, kind: str) -> None:
    seen: set[str] = set()
    for record in records:
        value = str(getattr(record, attr))
        if value in seen:
            raise ValueError(f"duplicate {kind} {attr}: {value}")
        seen.add(value)


def load_portfolio_corpus(
    path: Path,
    *,
    public_safe: bool = False,
    complete: bool = False,
) -> PortfolioCorpusSnapshot:
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8", "strict"))
    validate_document("portfolio-corpus", payload)
    assert isinstance(payload, dict)

    counts = PortfolioCounts.from_mapping(payload["counts"])
    counts.validate()
    workstream_counts = WorkstreamCounts.from_mapping(payload["workstream_counts"])
    workstream_counts.validate()

    private_inventory = payload["private_inventory"]
    private_workstream_inventory = payload["private_workstream_inventory"]
    assert isinstance(private_inventory, dict)
    assert isinstance(private_workstream_inventory, dict)
    private_count = int(private_inventory["count"])
    private_scheme = str(private_inventory["public_commitment_scheme"])
    private_committed = bool(private_inventory["exact_membership_publicly_committed"])
    private_workstream_count = int(private_workstream_inventory["count"])
    private_workstream_scheme = str(private_workstream_inventory["public_commitment_scheme"])
    private_workstream_committed = bool(private_workstream_inventory["exact_membership_publicly_committed"])
    if private_count != counts.private:
        raise ValueError(
            "private inventory count does not match portfolio private count"
        )
    if private_workstream_count != workstream_counts.private:
        raise ValueError(
            "private workstream inventory count does not match "
            "workstream private count"
        )

    records = tuple(
        PortfolioRecord.from_mapping(item) for item in payload["records"]
    )
    workstreams = tuple(
        WorkstreamRecord.from_mapping(item) for item in payload["workstreams"]
    )
    _reject_duplicate(records, "id", "portfolio")
    _reject_duplicate(records, "repository", "portfolio")
    _reject_duplicate(workstreams, "id", "workstream")

    public_records = sum(record.visibility == "public" for record in records)
    private_records = sum(record.visibility == "private" for record in records)
    archived_records = sum(record.archived for record in records)
    public_workstreams = sum(
        item.visibility == "public" for item in workstreams
    )
    private_workstreams = sum(
        item.visibility == "private" for item in workstreams
    )

    if public_safe:
        if private_records:
            raise ValueError(
                "public-safe portfolio corpus contains private repository records"
            )
        if private_workstreams:
            raise ValueError(
                "public-safe portfolio corpus contains private workstream records"
            )
        if len(records) != counts.public:
            raise ValueError(
                "public-safe corpus must enumerate every public repository"
            )
        if len(workstreams) != workstream_counts.public:
            raise ValueError(
                "public-safe corpus must enumerate every public workstream"
            )
        if archived_records != counts.public_archived:
            raise ValueError("public-safe archived record count mismatch")
    elif complete:
        if len(records) != counts.total:
            raise ValueError(
                "complete portfolio corpus must enumerate every repository"
            )
        if public_records != counts.public or private_records != counts.private:
            raise ValueError(
                "complete portfolio visibility counts do not match records"
            )
        if archived_records != counts.archived:
            raise ValueError("complete portfolio archived count mismatch")
        if len(workstreams) != workstream_counts.total:
            raise ValueError(
                "complete portfolio corpus must enumerate every workstream"
            )
        if (
            public_workstreams != workstream_counts.public
            or private_workstreams != workstream_counts.private
        ):
            raise ValueError(
                "complete workstream visibility counts do not match records"
            )

    return PortfolioCorpusSnapshot(
        corpus_id=str(payload["corpus_id"]),
        observed_at=str(payload["observed_at"]),
        owner=str(payload["owner"]),
        status_basis=(
            str(payload["status_basis"])
            if payload.get("status_basis")
            else None
        ),
        freshness_rule=(
            str(payload["freshness_rule"])
            if payload.get("freshness_rule")
            else None
        ),
        counts=counts,
        workstream_counts=workstream_counts,
        private_inventory_count=private_count,
        private_inventory_scheme=private_scheme,
        private_inventory_exact_membership_publicly_committed=private_committed,
        private_workstream_inventory_count=private_workstream_count,
        private_workstream_inventory_scheme=private_workstream_scheme,
        private_workstream_exact_membership_publicly_committed=private_workstream_committed,
        records=records,
        workstreams=workstreams,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
    )
