"""Crash/response-loss observation only; never authorizes retry or dispatch."""
import hashlib
import hmac
import json
import sqlite3
import time

import pytest

from portal.executor_readonly_envelope import (
    ReadonlyEnvelopeTrust,
    ReservationStorageError,
    verify_and_reserve_readonly_envelope,
    reconcile_consumed_readonly_envelope,
)

KEY = b"synthetic-reconciliation-only-issuer-key-32-bytes"
DOMAIN = b"PORTAL_EXECUTOR_READONLY_ENVELOPE_V1\x00"


def fixture(tmp_path, **changes):
    now = int(time.time())
    raw = {
        "schema": "PORTAL_EXECUTOR_READONLY_ENVELOPE_V1",
        "issuer_id": "issuer-A",
        "operation": "READ_GITHUB_REFERENCE_METADATA",
        "effect_ceiling": "NO_EFFECT",
        "repository": "thebrazenbeard/project-runner",
        "ref": "refs/heads/main",
        "exact_head": "a" * 40,
        "target_id": "Lappy",
        "dispatch_id": "dispatch-1",
        "attempt_id": "attempt-1",
        "nonce": "1" * 32,
        "not_before": now - 5,
        "expires_at": now + 60,
    }
    raw.update(changes)
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    signature = hmac.new(KEY, DOMAIN + encoded, hashlib.sha256).hexdigest()
    expected = {key:raw[key] for key in (
        "issuer_id", "repository", "ref", "exact_head", "target_id",
        "dispatch_id", "attempt_id"
    )}
    trust = ReadonlyEnvelopeTrust(
        issuer_keys={"issuer-A":KEY},nonce_db=tmp_path/"reservations.sqlite3"
    )
    return encoded, signature, expected, trust


def reserve(raw,sig,expected,trust):
    return verify_and_reserve_readonly_envelope(
        raw,sig,trust=trust,expected=expected
    )


def reconcile(raw,sig,expected,trust):
    return reconcile_consumed_readonly_envelope(
        raw,sig,trust=trust,expected=expected
    )


def test_missing_database_is_unknown_storage_not_retry_permission(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    with pytest.raises(ReservationStorageError):
        reconcile(raw,sig,expected,trust)
    assert not trust.nonce_db.exists()


def test_committed_lost_response_returns_terminal_not_new_grant(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    assert reconcile(raw,sig,expected,trust).status=="CONSUMED_EXACT_DO_NOT_REDISPATCH"
    with pytest.raises(ValueError,match="replay"):
        reserve(raw,sig,expected,trust)


def test_committed_reservation_can_be_observed_after_expiry(tmp_path,monkeypatch):
    from portal import executor_readonly_envelope as module
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    monkeypatch.setattr(module.time,"time",lambda:float(json.loads(raw)["expires_at"]+100))
    assert reconcile(raw,sig,expected,trust).status=="CONSUMED_EXACT_DO_NOT_REDISPATCH"


def test_missing_record_in_intact_database_is_never_permission(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    other,other_sig,other_expected,_=fixture(
        tmp_path,nonce="2"*32,dispatch_id="dispatch-2",attempt_id="attempt-2"
    )
    with pytest.raises(ReservationStorageError,match="not observed"):
        reconcile(other,other_sig,other_expected,trust)


@pytest.mark.parametrize("changes",[
    {"exact_head":"b"*40},
    {"nonce":"2"*32},
    {"dispatch_id":"dispatch-2"},
    {"attempt_id":"attempt-2"},
])
def test_identifier_or_digest_collision_is_integrity_conflict(tmp_path,changes):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    other,other_sig,other_expected,_=fixture(tmp_path,**changes)
    with pytest.raises(ReservationStorageError,match="conflict"):
        reconcile(other,other_sig,other_expected,trust)


def test_corrupt_signature_rejected_before_storage_read(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    with pytest.raises(ValueError,match="signature"):
        reconcile(raw,"f"*64,expected,trust)


def test_independent_head_pin_mismatch_rejected(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    with pytest.raises(ValueError,match="pinned"):
        reconcile(raw,sig,{**expected,"exact_head":"b"*40},trust)


def test_read_only_lookup_does_not_change_database_bytes(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    before=hashlib.sha256(trust.nonce_db.read_bytes()).hexdigest()
    assert reconcile(raw,sig,expected,trust).status=="CONSUMED_EXACT_DO_NOT_REDISPATCH"
    after=hashlib.sha256(trust.nonce_db.read_bytes()).hexdigest()
    assert before==after


def test_trigger_tampering_is_storage_error(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    with sqlite3.connect(trust.nonce_db) as db:
        db.execute(
            "CREATE TRIGGER unsafe AFTER UPDATE ON readonly_envelope_reservations "
            "BEGIN SELECT 1; END"
        )
    with pytest.raises(ReservationStorageError,match="trigger"):
        reconcile(raw,sig,expected,trust)


def test_lookup_must_not_create_missing_schema(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    sqlite3.connect(trust.nonce_db).close()
    with pytest.raises(ReservationStorageError,match="schema|missing"):
        reconcile(raw,sig,expected,trust)
    with sqlite3.connect(trust.nonce_db) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]==0


def test_observation_is_never_truthy_or_dispatch_capability(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    observation=reconcile(raw,sig,expected,trust)
    with pytest.raises(TypeError,match="authorization"):
        bool(observation)
    assert not hasattr(observation,"target_id")
    assert not hasattr(observation,"dispatch_id")
    assert not hasattr(observation,"operation")
    assert observation.execution_authorized is False
    assert observation.host_authenticated is False


def test_reservation_result_is_not_truthy_authorization(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    result=reserve(raw,sig,expected,trust)
    with pytest.raises(TypeError,match="authorization"):
        bool(result)


def test_invalid_signature_wins_over_missing_database(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    with pytest.raises(ValueError,match="signature"):
        reconcile(raw,"f"*64,expected,trust)
    assert not trust.nonce_db.exists()


def test_directory_auxiliary_files_unchanged_by_readonly_lookup(tmp_path):
    raw,sig,expected,trust=fixture(tmp_path)
    reserve(raw,sig,expected,trust)
    def snapshot():
        return {
            p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.iterdir() if p.is_file()
        }
    before=snapshot()
    observation=reconcile(raw,sig,expected,trust)
    assert observation.status=="CONSUMED_EXACT_DO_NOT_REDISPATCH"
    assert before==snapshot()
