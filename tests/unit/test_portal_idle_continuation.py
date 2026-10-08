"""Contract tests for durable, host-fed, idle continuation admission."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from portal.idle_continuation import PortalIdleGate


def test_turn_arms_at_reply_completion_and_claims_once_after_sixty_seconds(tmp_path):
    gate = PortalIdleGate(tmp_path / "idle.sqlite3")
    assert gate.record_input(input_id="input-1") == 1
    assert gate.record_reply_complete(
        input_id="input-1", reply_id="reply-1", completed_at=100
    )
    assert gate.claim_if_idle(now=159.999, claimed_by="lappy") is None
    ticket = gate.claim_if_idle(now=160, claimed_by="lappy")
    assert ticket is not None
    assert ticket.input_epoch == 1
    assert ticket.input_id == "input-1"
    assert ticket.claimed_by == "lappy"
    assert gate.claim_if_idle(now=999, claimed_by="worklaptop") is None


def test_new_input_cancels_pending_reply_and_stale_completion(tmp_path):
    gate = PortalIdleGate(tmp_path / "idle.sqlite3")
    gate.record_input(input_id="a")
    assert gate.record_reply_complete(
        input_id="a", reply_id="ra", completed_at=1
    )
    gate.record_input(input_id="b")
    assert gate.claim_if_idle(now=999, claimed_by="lappy") is None
    assert not gate.record_reply_complete(
        input_id="a", reply_id="stale", completed_at=999
    )
    assert gate.record_reply_complete(
        input_id="b", reply_id="rb", completed_at=1000
    )
    assert gate.claim_if_idle(now=1060, claimed_by="lappy") is not None


def test_input_retry_does_not_disarm_and_reply_retry_does_not_extend(tmp_path):
    gate = PortalIdleGate(tmp_path / "idle.sqlite3")
    assert gate.record_input(input_id="a") == 1
    assert gate.record_reply_complete(
        input_id="a", reply_id="ra", completed_at=20
    )
    assert gate.record_input(input_id="a") == 1
    assert gate.record_reply_complete(
        input_id="a", reply_id="ra", completed_at=70
    )
    assert gate.claim_if_idle(now=80, claimed_by="host") is not None
    assert not gate.record_reply_complete(
        input_id="a", reply_id="ra", completed_at=200
    )


def test_crash_reopen_does_not_replay_a_claim(tmp_path):
    path = tmp_path / "idle.sqlite3"
    a = PortalIdleGate(path)
    a.record_input(input_id="a")
    a.record_reply_complete(input_id="a", reply_id="ra", completed_at=10)
    original = a.claim_if_idle(now=70, claimed_by="lappy")
    assert original is not None
    b = PortalIdleGate(path)
    assert b.claim_if_idle(now=120, claimed_by="other") is None
    assert b.record_input(input_id="b") == 2
    b.record_reply_complete(input_id="b", reply_id="rb", completed_at=150)
    replacement = b.claim_if_idle(now=210, claimed_by="other")
    assert replacement is not None
    assert replacement.ticket_id != original.ticket_id
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM portal_idle_claims").fetchone()[0] == 2


def test_two_host_claimants_are_serialized(tmp_path):
    path = tmp_path / "idle.sqlite3"
    a = PortalIdleGate(path)
    a.record_input(input_id="a")
    a.record_reply_complete(input_id="a", reply_id="ra", completed_at=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda name: PortalIdleGate(path).claim_if_idle(
                now=70, claimed_by=name
            ), ("lappy", "worklaptop")
        ))
    assert sum(result is not None for result in results) == 1


def test_rejects_untrusted_or_invalid_events(tmp_path):
    with pytest.raises(ValueError):
        PortalIdleGate(tmp_path / "idle.sqlite3", idle_seconds=0)
    gate = PortalIdleGate(tmp_path / "idle.sqlite3")
    for invalid in ("", "../other", "hello world", "a" * 129):
        with pytest.raises(ValueError):
            gate.record_input(input_id=invalid)
    gate.record_input(input_id="a")
    with pytest.raises(ValueError):
        gate.record_reply_complete(input_id="a", reply_id="r", completed_at=float("nan"))
    with pytest.raises(ValueError):
        gate.claim_if_idle(now=-1, claimed_by="lappy")


def test_ticket_never_contains_effect_authority(tmp_path):
    gate = PortalIdleGate(tmp_path / "idle.sqlite3")
    gate.record_input(input_id="a")
    gate.record_reply_complete(input_id="a", reply_id="ra", completed_at=10)
    ticket = gate.claim_if_idle(now=70, claimed_by="lappy")
    assert ticket is not None
    assert not any("authority" in key or "effect" in key or "spend" in key
                   for key in vars(ticket))
