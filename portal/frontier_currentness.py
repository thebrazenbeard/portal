from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable

from runner.portfolio_advancement import AdvancementItem, AdvancementWave


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DISPOSITIONS = frozenset({"CURRENT", "HELD"})
_INERT_ACTIONS = frozenset({"PRESERVE_ONLY", "REFRESH_IF_REACTIVATED"})


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _canonical_sha256(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def frontier_policy_sha256(item: AdvancementItem) -> str:
    """Hash the complete scheduling/semantic policy attached to one frontier."""
    return _canonical_sha256(
        {
            "subject_kind": item.subject_kind,
            "subject_id": item.subject_id,
            "repositories": list(item.repositories),
            "priority": item.priority,
            "family_id": item.family_id,
            "activity_state": item.activity_state,
            "lead_identity": item.lead_identity,
            "reviewer_identities": list(item.reviewer_identities),
            "action": item.action,
            "execution_state": item.execution_state,
            "effect_ceiling": item.effect_ceiling,
            "review_gate": item.review_gate,
            "frontier": item.frontier,
            "source_status": item.source_status,
            "lane_id": item.lane_id,
        }
    )


@dataclass(frozen=True)
class PortalFrontierObservation:
    subject_kind: str
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    frontier_sha256: str
    disposition: str = "CURRENT"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_kind",
            _required(self.subject_kind, "subject_kind"),
        )
        object.__setattr__(
            self,
            "subject_id",
            _required(self.subject_id, "subject_id"),
        )
        repository = _required(self.repository, "repository")
        if repository.count("/") != 1:
            raise ValueError("repository must use owner/name")
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "ref", _required(self.ref, "ref"))
        exact_head = _required(self.exact_head, "exact_head").lower()
        if _SHA40.fullmatch(exact_head) is None:
            raise ValueError("exact_head must be a lowercase 40-character sha")
        object.__setattr__(self, "exact_head", exact_head)
        frontier_sha256 = _required(
            self.frontier_sha256,
            "frontier_sha256",
        ).lower()
        if _SHA256.fullmatch(frontier_sha256) is None:
            raise ValueError("frontier_sha256 must be a lowercase sha256")
        object.__setattr__(self, "frontier_sha256", frontier_sha256)
        disposition = _required(self.disposition, "disposition").upper()
        if disposition not in _DISPOSITIONS:
            raise ValueError("unsupported frontier disposition")
        object.__setattr__(self, "disposition", disposition)


@dataclass(frozen=True)
class PortalFrontierCurrentnessSnapshot:
    current_subjects: tuple[tuple[str, str], ...]
    excluded_subjects: tuple[tuple[str, str], ...]
    reasons: dict[tuple[str, str], str]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_host_frontier_observations (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    ref TEXT NOT NULL,
    exact_head TEXT NOT NULL,
    frontier_sha256 TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('CURRENT', 'HELD')),
    observed_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (subject_kind, subject_id)
);
"""


class PortalHostFrontierStore:
    """Durable, expiring host semantic-currentness observations."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> PortalFrontierObservation:
        return PortalFrontierObservation(
            subject_kind=str(row["subject_kind"]),
            subject_id=str(row["subject_id"]),
            repository=str(row["repository"]),
            ref=str(row["ref"]),
            exact_head=str(row["exact_head"]),
            frontier_sha256=str(row["frontier_sha256"]),
            disposition=str(row["disposition"]),
        )

    def advertise(
        self,
        observation: PortalFrontierObservation,
        *,
        ttl_seconds: float,
        observed_at: float | None = None,
    ) -> PortalFrontierObservation:
        if ttl_seconds <= 0:
            raise ValueError("frontier observation ttl must be positive")
        observed = float(time.time() if observed_at is None else observed_at)
        expires = observed + float(ttl_seconds)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT *
                FROM portal_host_frontier_observations
                WHERE subject_kind = ? AND subject_id = ?
                """,
                (observation.subject_kind, observation.subject_id),
            ).fetchone()
            if row is not None:
                existing_observed = float(row["observed_at"])
                if observed < existing_observed:
                    raise ValueError("stale host frontier observation")
                if observed == existing_observed:
                    existing = self._observation_from_row(row)
                    if existing != observation:
                        raise ValueError(
                            "conflicting host frontier observation"
                        )
                    self.connection.commit()
                    return existing

            self.connection.execute(
                """
                INSERT INTO portal_host_frontier_observations(
                    subject_kind, subject_id, repository, ref, exact_head,
                    frontier_sha256, disposition, observed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_kind, subject_id) DO UPDATE SET
                    repository = excluded.repository,
                    ref = excluded.ref,
                    exact_head = excluded.exact_head,
                    frontier_sha256 = excluded.frontier_sha256,
                    disposition = excluded.disposition,
                    observed_at = excluded.observed_at,
                    expires_at = excluded.expires_at
                """,
                (
                    observation.subject_kind,
                    observation.subject_id,
                    observation.repository,
                    observation.ref,
                    observation.exact_head,
                    observation.frontier_sha256,
                    observation.disposition,
                    observed,
                    expires,
                ),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return observation

    def active(
        self,
        *,
        now: float | None = None,
    ) -> dict[tuple[str, str], PortalFrontierObservation]:
        observed_now = float(time.time() if now is None else now)
        rows = self.connection.execute(
            """
            SELECT *
            FROM portal_host_frontier_observations
            WHERE observed_at <= ? AND expires_at > ?
            ORDER BY subject_kind, subject_id
            """,
            (observed_now, observed_now),
        ).fetchall()
        return {
            (str(row["subject_kind"]), str(row["subject_id"])):
                self._observation_from_row(row)
            for row in rows
        }


class PortalHostFrontierCurrentness:
    """Classify queued repository frontiers against live semantic evidence."""

    def __init__(
        self,
        *,
        store: PortalHostFrontierStore,
        head_reader: Callable[[str, str], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.head_reader = head_reader
        self.clock = clock

    def classify(
        self,
        wave: AdvancementWave,
    ) -> PortalFrontierCurrentnessSnapshot:
        observations = self.store.active(now=float(self.clock()))
        current: list[tuple[str, str]] = []
        excluded: list[tuple[str, str]] = []
        reasons: dict[tuple[str, str], str] = {}

        for item in sorted(
            wave.items,
            key=lambda value: (value.subject_kind, value.subject_id),
        ):
            if (
                item.subject_kind != "repository"
                or item.execution_state != "QUEUED"
                or item.action in _INERT_ACTIONS
            ):
                continue

            key = (item.subject_kind, item.subject_id)
            observation = observations.get(key)
            if observation is None:
                excluded.append(key)
                reasons[key] = "MISSING_FRONTIER_CURRENTNESS"
                continue
            if (
                item.repositories != (observation.repository,)
                or observation.frontier_sha256 != frontier_policy_sha256(item)
            ):
                excluded.append(key)
                reasons[key] = "STALE_FRONTIER_POLICY"
                continue
            if observation.disposition == "HELD":
                excluded.append(key)
                reasons[key] = "HOST_FRONTIER_HELD"
                continue

            observed_head = self.head_reader(
                observation.repository,
                observation.ref,
            )
            if observed_head != observation.exact_head:
                excluded.append(key)
                reasons[key] = "STALE_FRONTIER_HEAD"
                continue
            current.append(key)

        return PortalFrontierCurrentnessSnapshot(
            current_subjects=tuple(current),
            excluded_subjects=tuple(excluded),
            reasons=reasons,
        )
