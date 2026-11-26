from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from .portfolio_corpus import PortfolioCorpusSnapshot
from .schema import validate_document


@dataclass(frozen=True)
class AdvancementItem:
    subject_kind: str
    subject_id: str
    repositories: tuple[str, ...]
    priority: str
    family_id: str
    activity_state: str
    lead_identity: str
    reviewer_identities: tuple[str, ...]
    action: str
    execution_state: str
    effect_ceiling: str
    review_gate: str
    frontier: str | None
    source_status: str
    lane_id: str | None = None

    @property
    def effective_lane(self) -> str:
        return self.lane_id or self.lead_identity

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "AdvancementItem":
        subject_kind = str(data["subject_kind"])
        if subject_kind == "repository":
            repositories = (str(data["repository"]),)
        elif subject_kind == "workstream":
            raw = data.get("repositories", [])
            if not isinstance(raw, list):
                raise ValueError("workstream repositories must be a list")
            repositories = tuple(str(value) for value in raw)
        else:
            raise ValueError("unsupported advancement subject kind")
        raw_reviewers = data.get("reviewer_identities", [])
        if not isinstance(raw_reviewers, list):
            raise ValueError("reviewer_identities must be a list")
        raw_lane = data.get("lane")
        lane_id = None
        if raw_lane is not None:
            lane_id = str(raw_lane).strip()
            if not lane_id:
                raise ValueError("lane must not be empty")
        return cls(
            subject_kind=subject_kind,
            subject_id=str(data["subject_id"]),
            repositories=repositories,
            priority=str(data["priority"]),
            family_id=str(data["family_id"]),
            activity_state=str(data["activity_state"]),
            lead_identity=str(data["lead_identity"]),
            reviewer_identities=tuple(str(value) for value in raw_reviewers),
            action=str(data["action"]),
            execution_state=str(data["execution_state"]),
            effect_ceiling=str(data["effect_ceiling"]),
            review_gate=str(data["review_gate"]),
            frontier=(
                str(data["frontier"])
                if data.get("frontier") is not None
                else None
            ),
            source_status=str(data["source_status"]),
            lane_id=lane_id,
        )


@dataclass(frozen=True)
class AdvancementWave:
    wave_id: str
    generated_at: str
    corpus_binding: Mapping[str, object]
    identities: Mapping[str, str]
    policy: Mapping[str, object]
    items: tuple[AdvancementItem, ...]

    def summary(self) -> dict[str, object]:
        return {
            "wave_id": self.wave_id,
            "generated_at": self.generated_at,
            "subjects": len(self.items),
            "queued": sum(item.execution_state == "QUEUED" for item in self.items),
            "held": sum(item.execution_state == "HELD" for item in self.items),
            "by_identity": dict(sorted(Counter(
                item.lead_identity for item in self.items
            ).items())),
            "by_lane": dict(sorted(Counter(
                item.effective_lane for item in self.items
            ).items())),
            "by_action": dict(sorted(Counter(
                item.action for item in self.items
            ).items())),
            "by_priority": dict(sorted(Counter(
                item.priority for item in self.items
            ).items())),
        }

    def for_identity(self, identity: str) -> tuple[AdvancementItem, ...]:
        return tuple(
            item for item in self.items
            if item.lead_identity == identity and item.execution_state == "QUEUED"
        )


def load_advancement_wave(path: Path) -> AdvancementWave:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_document("portfolio-advancement", payload)
    assert isinstance(payload, dict)
    items = tuple(AdvancementItem.from_mapping(item) for item in payload["items"])
    identities = {str(k): str(v) for k, v in payload["identities"].items()}
    policy = dict(payload["policy"])
    binding = dict(payload["corpus_binding"])

    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.subject_kind, item.subject_id)
        if key in seen:
            raise ValueError(
                f"duplicate advancement subject: {item.subject_kind}:{item.subject_id}"
            )
        seen.add(key)
        if item.lead_identity not in identities:
            raise ValueError(
                f"unknown lead identity for {item.subject_kind}:{item.subject_id}"
            )
        if item.lead_identity in item.reviewer_identities:
            raise ValueError(
                f"lead identity cannot review itself for {item.subject_kind}:{item.subject_id}"
            )
        unknown_reviewers = set(item.reviewer_identities) - set(identities)
        if unknown_reviewers:
            raise ValueError(
                f"unknown reviewer identity for {item.subject_kind}:{item.subject_id}"
            )
        if item.effect_ceiling not in {"SOURCE_ONLY", "NO_EFFECT"}:
            raise ValueError("advancement wave exceeds source-only effect ceiling")
        if item.activity_state in {"ARCHIVED", "SUPERSEDED"}:
            if item.action != "PRESERVE_ONLY" or item.execution_state != "HELD":
                raise ValueError(
                    "archived/superseded subjects must be held as PRESERVE_ONLY"
                )

    return AdvancementWave(
        wave_id=str(payload["wave_id"]),
        generated_at=str(payload["generated_at"]),
        corpus_binding=binding,
        identities=identities,
        policy=policy,
        items=items,
    )


def validate_wave_against_corpus(
    wave: AdvancementWave,
    corpus: PortfolioCorpusSnapshot,
    *,
    public_safe: bool,
) -> None:
    binding = wave.corpus_binding
    if binding.get("binding_kind") == "LOCAL_SHA256":
        if str(binding.get("sha256", "")) != corpus.sha256:
            raise ValueError("wave local corpus sha256 does not match corpus")
    if int(binding["total_repository_count"]) != corpus.counts.total:
        raise ValueError("wave total repository count does not match corpus")
    if int(binding["private_repository_count"]) != corpus.counts.private:
        raise ValueError("wave private repository count does not match corpus")
    if int(binding["total_workstream_count"]) != corpus.workstream_counts.total:
        raise ValueError("wave total workstream count does not match corpus")
    if int(binding["private_workstream_count"]) != corpus.workstream_counts.private:
        raise ValueError("wave private workstream count does not match corpus")

    repo_items = {
        item.subject_id: item
        for item in wave.items
        if item.subject_kind == "repository"
    }
    workstream_items = {
        item.subject_id: item
        for item in wave.items
        if item.subject_kind == "workstream"
    }
    expected_repositories = {record.id: record for record in corpus.records}
    expected_workstreams = {record.id: record for record in corpus.workstreams}

    if set(repo_items) != set(expected_repositories):
        raise ValueError("wave repository membership does not exactly match corpus")
    if set(workstream_items) != set(expected_workstreams):
        raise ValueError("wave workstream membership does not exactly match corpus")

    for subject_id, record in expected_repositories.items():
        item = repo_items[subject_id]
        if item.repositories != (record.repository,):
            raise ValueError(f"wave repository binding drift for {subject_id}")
        if item.priority != record.priority.value:
            raise ValueError(f"wave priority drift for {subject_id}")
        if item.family_id != record.family_id:
            raise ValueError(f"wave family drift for {subject_id}")
        if item.activity_state != record.activity_state.value:
            raise ValueError(f"wave activity drift for {subject_id}")
        if item.frontier != record.current_frontier:
            raise ValueError(f"wave frontier drift for {subject_id}")

    for subject_id, record in expected_workstreams.items():
        item = workstream_items[subject_id]
        if item.repositories != record.durable_surfaces:
            raise ValueError(f"wave workstream surface drift for {subject_id}")
        if item.priority != record.priority.value:
            raise ValueError(f"wave workstream priority drift for {subject_id}")
        if item.family_id != record.family_id:
            raise ValueError(f"wave workstream family drift for {subject_id}")
        if item.activity_state != record.activity_state.value:
            raise ValueError(f"wave workstream activity drift for {subject_id}")
        if item.frontier != record.current_frontier:
            raise ValueError(f"wave workstream frontier drift for {subject_id}")

    if public_safe:
        if len(repo_items) != corpus.counts.public:
            raise ValueError("public wave must cover every public repository")
        if len(workstream_items) != corpus.workstream_counts.public:
            raise ValueError("public wave must cover every public workstream")
