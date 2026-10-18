"""Current Runner guarantees; unsupported independent-root assertion stays explicit
in tests/diagnostics/runner_claim_concurrency_probe.py and are run separately.
"""
import importlib.util
from pathlib import Path
import pytest

_path = Path(__file__).resolve().parents[1] / "diagnostics/runner_claim_concurrency_probe.py"
_spec = importlib.util.spec_from_file_location("runner_claim_concurrency_probe", _path)
_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_probe)

test_same_fingerprint_same_database_has_one_admission = _probe.test_same_fingerprint_same_database_has_one_admission
test_distinct_fingerprints_same_key_have_one_local_owner = _probe.test_distinct_fingerprints_same_key_have_one_local_owner
test_independent_roots_expose_unshared_lease_scope = _probe.test_independent_roots_expose_unshared_lease_scope
test_same_fingerprint_independent_roots_have_independent_token_one = _probe.test_same_fingerprint_independent_roots_have_independent_token_one
test_interrupted_uncommitted_admission_rolls_back_on_cold_open = _probe.test_interrupted_uncommitted_admission_rolls_back_on_cold_open
test_committed_abrupt_exit_cold_recovery_no_duplicate_then_expired_refence = _probe.test_committed_abrupt_exit_cold_recovery_no_duplicate_then_expired_refence
test_superseded_fence_and_stale_generation_cannot_advance_work = _probe.test_superseded_fence_and_stale_generation_cannot_advance_work
test_simultaneous_expired_claim_recovery_has_one_new_fence = _probe.test_simultaneous_expired_claim_recovery_has_one_new_fence
test_initial_admission_numeric_clock_cold_replay = _probe.test_initial_admission_numeric_clock_cold_replay
test_refenced_admission_numeric_clock_cold_replay = _probe.test_refenced_admission_numeric_clock_cold_replay


def test_integer_refence_reconciliation_journal_remains_readable(tmp_path):
    path = tmp_path / "reconcile.db"
    _probe.seed(path)
    first = _probe.finish(_probe.launch(_probe.child_args(path, "a", "lineage-a", "first")))
    second = _probe.finish(_probe.launch(_probe.child_args(path, "a", "lineage-a", "second", mode="recover", now=111)))
    assert first["ok"] and second["ok"]
    store = _probe.SqliteDispatchAdmissionStore(path)
    try:
        reconciliation = store.load_latest_reconciliation(lineage_id="lineage-a",
            work_fingerprint_value=_probe.work_unit_fingerprint(_probe.work()), fencing_token=1)
        assert reconciliation.outcome == "NO_EFFECT_CONFIRMED"
    finally:
        store.close()


def test_historical_mismatched_attempt_is_not_silently_repaired(tmp_path):
    from runner.durable_dispatch import _attempt_digest
    path = tmp_path / "legacy-shaped.db"
    _probe.seed(path)
    first = _probe.finish(_probe.launch(_probe.child_args(path, "a", "lineage-a", "first")))
    assert first["ok"]
    fp = _probe.work_unit_fingerprint(_probe.work())
    # Explicit legacy-shaped corruption in a disposable fixture only. The old
    # integer hash differs from REAL100.0; no migration or fallback is granted.
    old_hash = _attempt_digest(lineage_id="lineage-a", work_fingerprint_value=fp,
        fencing_token=1, holder="first", admitted_at=100, budget_generation=2, work_generation=2)
    store = _probe.SqliteDispatchAdmissionStore(path)
    try:
        store.connection.execute("UPDATE execution_attempts SET attempt_sha256=?", (old_hash,))
        before = store.connection.execute("SELECT * FROM execution_attempts").fetchall()
        with pytest.raises(ValueError, match="journal digest mismatch"):
            store.recover_claim_only_root(budget=_probe.budget(), work=_probe.work(),
                effective_capabilities=("read",), holder="first", now=101.0, ttl=10)
        assert store.connection.execute("SELECT * FROM execution_attempts").fetchall() == before
    finally:
        store.close()
