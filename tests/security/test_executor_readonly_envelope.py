"""Synthetic-only tests: signed metadata envelope does not grant execution."""
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from portal.executor_readonly_envelope import (
    ReadonlyEnvelopeTrust, verify_and_reserve_readonly_envelope,
    _verify_and_reserve_with_clock,
)


KEY = b"synthetic-source-only-test-key-32bytes-not-production"
DOMAIN = b"PORTAL_EXECUTOR_READONLY_ENVELOPE_V1\x00" 


def make_payload(**changes):
    payload = {
        "schema": "PORTAL_EXECUTOR_READONLY_ENVELOPE_V1",
        "issuer_id": "offline-fixture-issuer",
        "operation": "READ_GITHUB_REFERENCE_METADATA",
        "effect_ceiling": "NO_EFFECT",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a" * 40,
        "target_id": "Lappy",
        "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
        "nonce": "1" * 32,
        "not_before": 100,
        "expires_at": 200,
    }
    payload.update(changes)
    return payload


def sign(payload, key=KEY):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return raw, hmac.new(key, DOMAIN + raw, hashlib.sha256).hexdigest()


def check(raw, signature, db: Path, **changes):
    expected = {
        "issuer_id": "offline-fixture-issuer",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a" * 40,
        "target_id": "Lappy",
        "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
    }
    expected.update(changes)
    return _verify_and_reserve_with_clock(
        raw, signature, trust=ReadonlyEnvelopeTrust(
            issuer_keys={"offline-fixture-issuer": KEY}, nonce_db=db,
        ), expected=expected, clock=lambda: 150,
    )


def test_valid_signed_metadata_envelope_reserves_once(tmp_path):
    raw, signature = sign(make_payload())
    db = tmp_path / "once.sqlite3"
    result = check(raw, signature, db)
    assert result.operation == "READ_GITHUB_REFERENCE_METADATA"
    assert result.effect_ceiling == "NO_EFFECT"
    assert result.exact_head == "a" * 40
    with pytest.raises(ValueError, match="replay"):
        check(raw, signature, db)


def test_reused_dispatch_cannot_switch_nonce_or_attempt(tmp_path):
    db = tmp_path / "once.sqlite3"
    raw, signature = sign(make_payload())
    check(raw, signature, db)
    other, other_mac = sign(make_payload(attempt_id="attempt-2", nonce="2"*32))
    with pytest.raises(ValueError, match="replay"):
        check(other, other_mac, db, attempt_id="attempt-2")


@pytest.mark.parametrize("mutation", [
    {"operation": "EXECUTE_COMMAND"},
    {"effect_ceiling": "SOURCE_ONLY"},
    {"target_id": "VN2"},
    {"repository": "thebrazenbeard/private"},
    {"exact_head": "b"*40},
    {"expires_at": 151, "not_before": 1},
    {"expires_at": 500},
    {"not_before": 151},
    {"authorized_effects": ["SOURCE_WRITE"]},
])
def test_mutations_fail_closed(tmp_path, mutation):
    raw, signature = sign(make_payload(**mutation))
    with pytest.raises(ValueError):
        check(raw, signature, tmp_path / "once.sqlite3")


def test_invalid_signature_does_not_consume_nonce(tmp_path):
    db = tmp_path / "once.sqlite3"
    raw, signature = sign(make_payload())
    with pytest.raises(ValueError, match="signature"):
        check(raw, "f"*64, db)
    check(raw, signature, db)


def test_duplicate_json_key_rejected_even_if_signed(tmp_path):
    raw, _ = sign(make_payload())
    text = raw.decode().replace('"operation":"READ_GITHUB_REFERENCE_METADATA"', '"operation":"EXECUTE_COMMAND","operation":"READ_GITHUB_REFERENCE_METADATA"')
    canonical = text.encode()
    signature = hmac.new(KEY, DOMAIN + canonical, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="duplicate"):
        check(canonical, signature, tmp_path / "once.sqlite3")


def test_refuses_unpinned_issuer(tmp_path):
    raw, signature = sign(make_payload(issuer_id="other-issuer"))
    with pytest.raises(ValueError):
        check(raw, signature, tmp_path / "once.sqlite3")


def test_size_bound_fails_without_creating_db(tmp_path):
    raw, signature = sign(make_payload(nonce="1"*6000))
    db = tmp_path / "once.sqlite3"
    with pytest.raises(ValueError, match="size"):
        check(raw, signature, db)
    assert not db.exists()


def test_two_concurrent_attempts_reserve_at_most_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    raw, sig = sign(make_payload())
    db = tmp_path / "once.sqlite3"

    def attempt(_):
        try:
            check(raw, sig, db)
            return "reserved"
        except ValueError as exc:
            if "replay" in str(exc):
                return "replay"
            raise

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sorted(results) == ["replay", "reserved"]


def test_nonce_reuse_with_different_dispatch_is_replay(tmp_path):
    db = tmp_path / "once.sqlite3"
    raw, mac = sign(make_payload())
    check(raw, mac, db)
    alt, signature = sign(make_payload(dispatch_id="d2", attempt_id="a2"))
    with pytest.raises(ValueError, match="replay"):
        check(alt, signature, db, dispatch_id="d2", attempt_id="a2")


@pytest.mark.parametrize("mutation", [
    {"not_before": True},
    {"expires_at": False},
    {"exact_head": "A"*40},
    {"ref": "refs/heads/../../evil"},
    {"nonce": "Z"*32},
    {"target_id": ""},
])
def test_invalid_types_and_identifiers_fail_closed(tmp_path, mutation):
    raw, signature = sign(make_payload(**mutation))
    with pytest.raises(ValueError):
        check(raw, signature, tmp_path / "once.sqlite3")


def test_wrong_key_rejected_without_nonce_reservation(tmp_path):
    db = tmp_path / "once.sqlite3"
    raw, signature = sign(make_payload(), key=b"other-pinned-test-key-32-bytes-not-production")
    with pytest.raises(ValueError, match="signature"):
        check(raw, signature, db)
    assert not db.exists()


def test_reserved_envelope_explicitly_does_not_authorize_execution(tmp_path):
    raw, signature = sign(make_payload())
    result = check(raw, signature, tmp_path / "once.sqlite3")
    assert result.execution_authorized is False
    assert result.host_authenticated is False


def test_preexisting_sqlite_trigger_cannot_clear_reservation(tmp_path):
    import sqlite3
    db = tmp_path / "once.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE readonly_envelope_reservations ("
            "issuer_id TEXT NOT NULL, nonce TEXT NOT NULL,"
            "dispatch_id TEXT NOT NULL,"
            "attempt_id TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "PRIMARY KEY (issuer_id, nonce),"
            "UNIQUE (issuer_id, dispatch_id),"
            "UNIQUE (issuer_id, attempt_id))"
        )
        conn.execute(
            "CREATE TRIGGER erase_reservation "
            "AFTER INSERT ON readonly_envelope_reservations "
            "BEGIN DELETE FROM readonly_envelope_reservations; END"
        )
    raw, signature = sign(make_payload())
    from portal.executor_readonly_envelope import ReservationStorageError
    with pytest.raises(ReservationStorageError, match="trigger"):
        check(raw, signature, db)


@pytest.mark.parametrize("repo", ["../repo", "owner/..", "./repo", "owner/."])
def test_path_like_repository_components_rejected_even_if_pinned(tmp_path, repo):
    raw, signature = sign(make_payload(repository=repo))
    with pytest.raises(ValueError, match="repository"):
        check(raw, signature, tmp_path / "once.sqlite3", repository=repo)


@pytest.mark.parametrize("ref", [
    "refs/heads/ok//evil",
    "refs/heads/ok/./evil",
    "refs/heads/.private",
    "refs/heads/a.lock",
])
def test_unsafe_git_ref_segments_rejected_even_if_pinned(tmp_path, ref):
    raw, signature = sign(make_payload(ref=ref))
    with pytest.raises(ValueError, match="ref"):
        check(raw, signature, tmp_path / "once.sqlite3", ref=ref)


def test_clock_is_checked_again_inside_write_transaction(tmp_path):
    raw, signature = sign(make_payload(not_before=100, expires_at=152))
    expected = {
        "issuer_id": "offline-fixture-issuer",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a"*40,
        "target_id": "Lappy",
        "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
    }
    readings = iter((150, 153))
    with pytest.raises(ValueError, match="expired"):
        _verify_and_reserve_with_clock(
            raw, signature, trust=ReadonlyEnvelopeTrust(
                issuer_keys={"offline-fixture-issuer": KEY},
                nonce_db=tmp_path / "once.sqlite3",
            ), expected=expected, clock=lambda: next(readings),
        )


def test_public_api_rejects_legacy_per_call_override_keywords(tmp_path):
    raw, sig = sign(make_payload())
    with pytest.raises(TypeError):
        verify_and_reserve_readonly_envelope(
            raw, sig, issuer_key=KEY, nonce_db=tmp_path/"different.sqlite3",
            expected={}, now=150,
        )


def test_trust_context_snapshot_does_not_allow_issuer_key_mutation(tmp_path):
    source = {"offline-fixture-issuer": KEY}
    trust = ReadonlyEnvelopeTrust(issuer_keys=source, nonce_db=tmp_path/"once.sqlite3")
    source["offline-fixture-issuer"] = b"x"*32
    assert trust.issuer_keys["offline-fixture-issuer"] == KEY
    with pytest.raises(TypeError):
        trust.issuer_keys["fake"] = b"z"*32


def test_unknown_issuer_cannot_supply_an_arbitrary_key(tmp_path):
    raw, sig = sign(make_payload(issuer_id="other-issuer"))
    trust = ReadonlyEnvelopeTrust(
        issuer_keys={"offline-fixture-issuer": KEY},
        nonce_db=tmp_path/"once.sqlite3",
    )
    expected = {
        "issuer_id": "other-issuer",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a"*40,
        "target_id": "Lappy", "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
    }
    with pytest.raises(ValueError, match="issuer"):
        _verify_and_reserve_with_clock(
            raw, sig, trust=trust, expected=expected, clock=lambda:150,
        )


def test_two_registered_issuers_may_reuse_dispatch_ids(tmp_path):
    db=tmp_path/"once.sqlite3"
    trust=ReadonlyEnvelopeTrust(
        issuer_keys={"offline-fixture-issuer":KEY, "other-issuer":b"separate-issuer-key-synthetic-fixture-32bytes"}, nonce_db=db
    )
    for issuer in ("offline-fixture-issuer","other-issuer"):
        key = KEY if issuer == "offline-fixture-issuer" else b"separate-issuer-key-synthetic-fixture-32bytes"
        raw,sig=sign(make_payload(issuer_id=issuer),key=key)
        expected={
          "issuer_id":issuer, "repository":"thebrazenbeard/project-runner",
          "ref":"refs/heads/main","exact_head":"a"*40,"target_id":"Lappy",
          "dispatch_id":"dispatch-1","attempt_id":"attempt-1",
        }
        result=_verify_and_reserve_with_clock(raw,sig,trust=trust,expected=expected,clock=lambda:150)
        assert result.issuer_id==issuer


def test_hmac_without_domain_prefix_is_rejected(tmp_path):
    raw,_=sign(make_payload())
    mac=hmac.new(KEY,raw,hashlib.sha256).hexdigest()
    with pytest.raises(ValueError,match="signature"):
        check(raw,mac,tmp_path/"once.sqlite3")


def test_production_public_api_uses_runtime_clock(monkeypatch,tmp_path):
    from portal import executor_readonly_envelope as module
    monkeypatch.setattr(module.time,"time",lambda:150)
    trust=ReadonlyEnvelopeTrust(
        issuer_keys={"offline-fixture-issuer":KEY},nonce_db=tmp_path/"once.sqlite3"
    )
    raw,sig=sign(make_payload())
    expected={
        "issuer_id":"offline-fixture-issuer",
        "repository":"thebrazenbeard/project-runner",
        "ref":"refs/heads/main",
        "exact_head":"a"*40,
        "target_id":"Lappy","dispatch_id":"dispatch-1","attempt_id":"attempt-1",
    }
    assert verify_and_reserve_readonly_envelope(raw,sig,trust=trust,expected=expected).execution_authorized is False


def test_expiry_after_insert_rolls_back_before_commit(tmp_path):
    import sqlite3
    raw, sig = sign(make_payload(expires_at=152))
    trust = ReadonlyEnvelopeTrust(
        issuer_keys={"offline-fixture-issuer": KEY},
        nonce_db=tmp_path / "once.sqlite3",
    )
    expected = {
        "issuer_id": "offline-fixture-issuer",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a"*40, "target_id": "Lappy",
        "dispatch_id": "dispatch-1", "attempt_id": "attempt-1",
    }
    timestamps = iter((150, 150, 153))
    with pytest.raises(ValueError, match="expired"):
        _verify_and_reserve_with_clock(
            raw, sig, trust=trust, expected=expected,
            clock=lambda: next(timestamps),
        )
    with sqlite3.connect(trust.nonce_db) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='readonly_envelope_reservations'"
        ).fetchone()
        count = (db.execute(
            "SELECT COUNT(*) FROM readonly_envelope_reservations"
        ).fetchone()[0] if exists else 0)
    assert count == 0


def test_corrupt_reservation_database_is_storage_error_not_replay(tmp_path):
    from portal.executor_readonly_envelope import ReservationStorageError
    db=tmp_path / "once.sqlite3"
    db.write_bytes(b"not a SQLite database")
    raw, sig=sign(make_payload())
    with pytest.raises(ReservationStorageError):
        check(raw,sig,db)


def test_duplicate_issuer_key_material_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate|unique"):
        ReadonlyEnvelopeTrust(
            issuer_keys={"issuer-A": KEY, "issuer-B": KEY},
            nonce_db=tmp_path/"once.sqlite3",
        )


def test_unexpected_unique_index_reports_storage_integrity_not_replay(tmp_path):
    import sqlite3
    from portal.executor_readonly_envelope import ReservationStorageError
    db = tmp_path/"once.sqlite3"
    first, mac = sign(make_payload())
    check(first,mac,db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE UNIQUE INDEX malicious_nonce_initial "
            "ON readonly_envelope_reservations(substr(nonce, 1, 1))"
        )
    second, signature = sign(make_payload(
        nonce="1"*31+"2", dispatch_id="dispatch-2", attempt_id="attempt-2"))
    with pytest.raises(ReservationStorageError, match="index|schema"):
        check(second, signature, db, dispatch_id="dispatch-2",attempt_id="attempt-2")


def test_preexisting_wrong_schema_reports_storage_error(tmp_path):
    import sqlite3
    from portal.executor_readonly_envelope import ReservationStorageError
    db=tmp_path/"once.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE readonly_envelope_reservations (issuer_id TEXT)")
    raw, signature=sign(make_payload())
    with pytest.raises(ReservationStorageError, match="schema"):
        check(raw,signature,db)
