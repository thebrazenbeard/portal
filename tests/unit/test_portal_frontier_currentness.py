from __future__ import annotations

from pathlib import Path

import pytest

from portal.frontier_currentness import (
    PortalFrontierObservation,
    PortalHostFrontierCurrentness,
    PortalHostFrontierStore,
    frontier_policy_sha256,
)
from runner.portfolio_advancement import AdvancementItem, AdvancementWave


def _item(
    subject_id: str,
    repository: str,
    *,
    frontier: str = "Advance bounded work.",
) -> AdvancementItem:
    return AdvancementItem(
        subject_kind="repository",
        subject_id=subject_id,
        repositories=(repository,),
        priority="P1",
        family_id="test-family",
        activity_state="ACTIVE",
        lead_identity="ONE",
        reviewer_identities=("REZON",),
        action="EXECUTE_FRONTIER",
        execution_state="QUEUED",
        effect_ceiling="SOURCE_ONLY",
        review_gate="EXACT_HEAD_REVIEW",
        frontier=frontier,
        source_status="Current test status.",
        lane_id=None,
    )


def _wave(*items: AdvancementItem) -> AdvancementWave:
    return AdvancementWave(
        wave_id="PROJECT_RUNNER_PORTFOLIO_ADVANCEMENT_WAVE_V1",
        generated_at="2026-10-05T00:00:00Z",
        corpus_binding={},
        identities={"ONE": "lead", "REZON": "review"},
        policy={},
        items=tuple(items),
    )


def _observation(
    item: AdvancementItem,
    *,
    exact_head: str = "a" * 40,
    disposition: str = "CURRENT",
) -> PortalFrontierObservation:
    return PortalFrontierObservation(
        subject_kind=item.subject_kind,
        subject_id=item.subject_id,
        repository=item.repositories[0],
        ref="main",
        exact_head=exact_head,
        frontier_sha256=frontier_policy_sha256(item),
        disposition=disposition,
    )


def test_missing_frontier_currentness_excludes_only_missing_subject(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    tattler = _item("tattler", "thebrazenbeard/tattler")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(
        _observation(portal),
        ttl_seconds=300.0,
        observed_at=100.0,
    )

    currentness = PortalHostFrontierCurrentness(
        store=store,
        head_reader=lambda repository, ref: "a" * 40,
        clock=lambda: 120.0,
    )
    snapshot = currentness.classify(_wave(portal, tattler))

    assert snapshot.current_subjects == (("repository", "portal"),)
    assert snapshot.excluded_subjects == (("repository", "tattler"),)
    assert snapshot.reasons == {
        ("repository", "tattler"): "MISSING_FRONTIER_CURRENTNESS",
    }
    store.close()


def test_exact_head_mismatch_excludes_frontier_without_blocking_others(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    tattler = _item("tattler", "thebrazenbeard/tattler")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(_observation(portal), ttl_seconds=300.0, observed_at=100.0)
    store.advertise(_observation(tattler), ttl_seconds=300.0, observed_at=100.0)

    heads = {
        "thebrazenbeard/portal": "b" * 40,
        "thebrazenbeard/tattler": "a" * 40,
    }
    snapshot = PortalHostFrontierCurrentness(
        store=store,
        head_reader=lambda repository, ref: heads[repository],
        clock=lambda: 120.0,
    ).classify(_wave(portal, tattler))

    assert snapshot.current_subjects == (("repository", "tattler"),)
    assert snapshot.excluded_subjects == (("repository", "portal"),)
    assert snapshot.reasons == {
        ("repository", "portal"): "STALE_FRONTIER_HEAD",
    }
    store.close()


def test_policy_change_invalidates_prior_frontier_attestation(
    tmp_path: Path,
) -> None:
    original = _item("portal", "thebrazenbeard/portal", frontier="Old frontier.")
    changed = _item("portal", "thebrazenbeard/portal", frontier="New frontier.")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(_observation(original), ttl_seconds=300.0, observed_at=100.0)

    snapshot = PortalHostFrontierCurrentness(
        store=store,
        head_reader=lambda repository, ref: "a" * 40,
        clock=lambda: 120.0,
    ).classify(_wave(changed))

    assert snapshot.current_subjects == ()
    assert snapshot.excluded_subjects == (("repository", "portal"),)
    assert snapshot.reasons == {
        ("repository", "portal"): "STALE_FRONTIER_POLICY",
    }
    store.close()


def test_host_can_explicitly_hold_a_current_frontier(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(
        _observation(portal, disposition="HELD"),
        ttl_seconds=300.0,
        observed_at=100.0,
    )

    snapshot = PortalHostFrontierCurrentness(
        store=store,
        head_reader=lambda repository, ref: "a" * 40,
        clock=lambda: 120.0,
    ).classify(_wave(portal))

    assert snapshot.excluded_subjects == (("repository", "portal"),)
    assert snapshot.reasons == {
        ("repository", "portal"): "HOST_FRONTIER_HELD",
    }
    store.close()


def test_stale_frontier_observation_cannot_overwrite_newer_evidence(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(_observation(portal), ttl_seconds=300.0, observed_at=200.0)

    with pytest.raises(ValueError, match="stale host frontier observation"):
        store.advertise(
            _observation(portal, disposition="HELD"),
            ttl_seconds=300.0,
            observed_at=100.0,
        )
    store.close()


def test_same_timestamp_conflicting_frontier_observation_is_refused(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(_observation(portal), ttl_seconds=300.0, observed_at=200.0)

    with pytest.raises(ValueError, match="conflicting host frontier observation"):
        store.advertise(
            _observation(portal, disposition="HELD"),
            ttl_seconds=300.0,
            observed_at=200.0,
        )
    store.close()


def test_expired_frontier_observation_is_missing_currentness(
    tmp_path: Path,
) -> None:
    portal = _item("portal", "thebrazenbeard/portal")
    store = PortalHostFrontierStore(tmp_path / "portal.sqlite3")
    store.advertise(_observation(portal), ttl_seconds=10.0, observed_at=100.0)

    snapshot = PortalHostFrontierCurrentness(
        store=store,
        head_reader=lambda repository, ref: "a" * 40,
        clock=lambda: 111.0,
    ).classify(_wave(portal))

    assert snapshot.current_subjects == ()
    assert snapshot.excluded_subjects == (("repository", "portal"),)
    assert snapshot.reasons == {
        ("repository", "portal"): "MISSING_FRONTIER_CURRENTNESS",
    }
    store.close()
