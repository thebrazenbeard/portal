"""Same-root collision admission uses actual SQLite transactions and cold workers."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from runner.budgets import BudgetEnvelope
from runner.backends import BackendResult
from runner.durable_dispatch import SqliteDispatchAdmissionStore, execute_admitted
from runner.durable_dispatch import _attempt_digest, _reconciliation_digest
from runner.leases import Lease
from runner.models import ExactSubject
from runner.persistent_state import SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def work(name, keys=("repository:shared",)):
    return WorkUnit(id=name, root_frontier_id="collision-test", parent_work_id=None,
        inputs=(ExactSubject(repository="fixture/test", ref="fixture", commit=name * 40),),
        operation="PORTFOLIO_BOUND_CLAIM", required_capabilities=("read",),
        collision_keys=keys, recursion_depth=0,
        budget_allocation={"children": 0, "active": 1, "retries": 0, "backend_jobs": 1},
        expected_outputs=("NO_EFFECT",), completion_criteria=("held",), status=WorkUnitStatus.PENDING,
        payload={"execution_authority": False, "protected_effects_authorized": False})


def budget(name):
    return BudgetEnvelope(lineage_id=name, max_depth=0, depth=0, remaining_children=0,
        remaining_active=1, remaining_retries=0, remaining_backend_jobs=1)


def seed(db, *items):
    store = SqliteDispatchAdmissionStore(db)
    try:
        for item in items:
            store.initialize_root(budget=budget(item.id), work=item, effective_capabilities=("read",))
    finally:
        store.close()


def admit(store, item, holder="fixture", now=100.):
    return store.admit(lineage_id=item.id, work_fingerprint_value=work_unit_fingerprint(item),
        budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
        holder=holder, now=now, ttl=10.)


def worker(db, name, gate, ready):
    store = SqliteDispatchAdmissionStore(db)
    try:
        Path(ready).write_text(str(os.getpid()))
        end = time.monotonic() + 20
        while not Path(gate).exists():
            if time.monotonic() > end:
                raise RuntimeError("gate timeout")
            time.sleep(.01)
        try:
            result = admit(store, work(name), holder=name)
        except ValueError as exc:
            return {"pid": os.getpid(), "ok": False, "reason": str(exc)}
        return {"pid": os.getpid(), "ok": True, "token": result.lease.fencing_token}
    finally:
        store.close()


def test_separate_process_different_fingerprints_one_shared_key(tmp_path):
    db = tmp_path / "runner.db"
    seed(db, work("a"), work("b"))
    gate = tmp_path / "gate"
    procs = []
    try:
        for name in ("a", "b"):
            ready = tmp_path / name
            procs.append(subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "worker",
                str(db), name, str(gate), str(ready)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True))
        end = time.monotonic() + 20
        while not all((tmp_path / name).exists() for name in ("a", "b")):
            assert time.monotonic() < end
            time.sleep(.01)
        gate.write_text("GO")
        outcomes = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=25)
            assert proc.returncode == 0, stderr
            outcomes.append(json.loads(stdout))
        (tmp_path / "outcomes.json").write_text(json.dumps(outcomes, indent=2))
        assert len({outcome["pid"] for outcome in outcomes}) == 2
        assert sum(outcome["ok"] for outcome in outcomes) == 1
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)


def test_disjoint_keys_both_admit(tmp_path):
    db = tmp_path / "runner.db"
    left, right = work("a", ("left",)), work("b", ("right",))
    seed(db, left, right)
    store = SqliteDispatchAdmissionStore(db)
    try:
        assert admit(store, left).lease.fencing_token == 1
        assert admit(store, right).lease.fencing_token == 1
    finally:
        store.close()


def test_expiry_alone_does_not_release_collision_owner(tmp_path):
    db = tmp_path / "runner.db"
    seed(db, work("a"), work("b"))
    store = SqliteDispatchAdmissionStore(db)
    try:
        admit(store, work("a"))
        with pytest.raises(ValueError, match="collision"):
            admit(store, work("b"), now=111.)
    finally:
        store.close()


@pytest.mark.parametrize("keys", [("",), (" key",), ("key ",), ("a", "a"), (1,), ("a\x00b",), ("a\x85b",), ("a\ud800b",)])
def test_invalid_keys_rejected_before_state_write(tmp_path, keys):
    db = tmp_path / "runner.db"
    store = SqliteDispatchAdmissionStore(db)
    try:
        with pytest.raises(ValueError, match="collision"):
            store.initialize_root(budget=budget("a"), work=work("a", keys), effective_capabilities=("read",))
        assert store.connection.execute("SELECT COUNT(*) FROM recursive_work_state").fetchone()[0] == 0
    finally:
        store.close()


def test_fenced_local_claim_cancellation_transfers_key_with_monotonic_generation(tmp_path):
    db = tmp_path / "runner.db"
    seed(db, work("a"), work("b"))
    store = SqliteDispatchAdmissionStore(db)
    try:
        leases = SqliteLeaseStore(db)
        try:
            first = leases.claim(work_unit_fingerprint(work("a")), holder="local-owner", now=100., ttl=10.)
        finally:
            leases.close()
        assert first is not None
        assert store.connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone() == (0,)
        assert store.connection.execute("SELECT generation FROM collision_reservations").fetchone() == (1,)
        # Local owner cancellation uses the existing exact-fence terminal CAS.
        # It is not an untrusted host's purported proof of no external effect.
        works = SqliteRecursiveWorkStore(db)
        try:
            works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(work("a")),
                expected_generation=1, status=WorkUnitStatus.CLAIMED, lease=first, now=100.5)
            works.finalize_terminal_status(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(work("a")),
                expected_generation=2, status=WorkUnitStatus.SUPERSEDED, lease=first, now=101.)
        finally:
            works.close()
        assert store.connection.execute("SELECT work_fingerprint,generation FROM collision_reservations").fetchone() == (None, 1)
        second = admit(store, work("b"), now=102.)
        assert second.lease.fencing_token == 1
        assert store.connection.execute("SELECT work_fingerprint,generation FROM collision_reservations").fetchone() == (work_unit_fingerprint(work("b")), 2)
        leases = SqliteLeaseStore(db)
        try:
            assert leases.heartbeat(first, now=103., ttl=10.) is None
            assert not leases.release(first, now=103.)
            assert not leases.complete(first, now=103.)
        finally:
            leases.close()
    finally:
        store.close()


@pytest.mark.parametrize("same_fingerprint", [True, False])
def test_legacy_released_lease_without_reconciliation_stays_held(tmp_path, same_fingerprint):
    db = tmp_path / "legacy.db"
    store = SqliteDispatchAdmissionStore(db)
    works = SqliteRecursiveWorkStore(db)
    try:
        store.initialize_root(budget=replace(budget("a"), remaining_active=2,
            remaining_backend_jobs=2, remaining_retries=1), work=work("a"), effective_capabilities=("read",))
        store.initialize_root(budget=budget("b"), work=work("b"), effective_capabilities=("read",))
        first = admit(store, work("a"))
        works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(work("a")),
            expected_generation=2, status=WorkUnitStatus.FAILED_RETRYABLE, lease=first.lease, now=101.)
        # Model an existing pre-reservation database released by the old generic
        # lease API. Neither missing rows nor a free lease prove NO_EFFECT.
        store.connection.execute("DELETE FROM collision_reservations")
        store.connection.execute("UPDATE leases SET holder=NULL, expires_at=0")
        with pytest.raises(ValueError, match="collision|reconciliation"):
            if same_fingerprint:
                store.admit(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(work("a")),
                    budget_scope_id="root", expected_budget_generation=2, expected_work_generation=3,
                    holder="new", now=111., ttl=10.)
            else:
                admit(store, work("b"), now=111.)
    finally:
        works.close()
        store.close()


@pytest.mark.parametrize("same_fingerprint", [True, False])
def test_later_no_effect_cannot_launder_older_unresolved_attempt(tmp_path, same_fingerprint):
    db = tmp_path / "old-retry.db"
    store = SqliteDispatchAdmissionStore(db)
    works = SqliteRecursiveWorkStore(db)
    fingerprint = work_unit_fingerprint(work("a"))
    try:
        store.initialize_root(budget=replace(budget("a"), remaining_active=3,
            remaining_backend_jobs=3, remaining_retries=2), work=work("a"), effective_capabilities=("read",))
        store.initialize_root(budget=budget("b"), work=work("b"), effective_capabilities=("read",))
        first = admit(store, work("a"))
        works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=fingerprint,
            expected_generation=2, status=WorkUnitStatus.OUTCOME_UNKNOWN, lease=first.lease, now=101.)
        # Historical Runner allowed retry of an unknown attempt after expiry.
        # Reconstruct that valid journal shape as disposable evidence, not a
        # claim producer or a way to grant authority to a copied journal.
        second_digest = _attempt_digest(lineage_id="a", work_fingerprint_value=fingerprint,
            fencing_token=2, holder="old-second", admitted_at=111., budget_generation=3, work_generation=4)
        store.connection.execute("INSERT INTO execution_attempts (lineage_id,work_fingerprint,fencing_token,holder,admitted_at,budget_generation,work_generation,attempt_sha256) VALUES (?,?,?,?,?,?,?,?)",
            ("a", fingerprint, 2, "old-second", 111., 3, 4, second_digest))
        store.connection.execute("UPDATE recursive_work_state SET status='CLAIMED',generation=4 WHERE lineage_id='a'")
        store.connection.execute("UPDATE lineage_budgets SET remaining_active=1,remaining_backend_jobs=1,remaining_retries=1,generation=3 WHERE lineage_id='a'")
        store.connection.execute("UPDATE leases SET holder='old-second',fencing_token=2,expires_at=121.")
        store.connection.execute("UPDATE collision_reservations SET holder='old-second',fencing_token=2,generation=2")
        # Preserve the historical unsafe conclusive row, which the tightened
        # API correctly refuses to create now. A public digest authenticates no
        # actor; this fixture must stay held when reopened by current source.
        reason = "only second attempt was observed not to execute"
        reconciliation = _reconciliation_digest(outcome="NO_EFFECT_CONFIRMED", reason=reason,
            evidence=("second-fence-only",), reconciler="disposable-fixture", observed_at=112.)
        store.connection.execute("INSERT INTO execution_reconciliations (lineage_id,work_fingerprint,fencing_token,sequence,outcome,reason,evidence_json,reconciler,observed_at,reconciliation_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a", fingerprint, 2, 1, "NO_EFFECT_CONFIRMED", reason, '["second-fence-only"]', "disposable-fixture", 112., reconciliation))
        store.connection.execute("UPDATE leases SET holder=NULL,expires_at=0")
        store.connection.execute("UPDATE collision_reservations SET work_fingerprint=NULL,holder=NULL,fencing_token=NULL")
        store.connection.execute("UPDATE recursive_work_state SET status='FAILED_RETRYABLE',generation=5 WHERE lineage_id='a'")
        with pytest.raises(ValueError, match="collision|reconciliation"):
            if same_fingerprint:
                store.admit(lineage_id="a", work_fingerprint_value=fingerprint, budget_scope_id="root",
                    expected_budget_generation=3, expected_work_generation=5, holder="new", now=113., ttl=10.)
            else:
                admit(store, work("b"), now=113.)
    finally:
        works.close()
        store.close()


@pytest.mark.parametrize("recorded_unknown", [False, True])
def test_running_claim_no_effect_strings_cannot_release_shared_key(tmp_path, recorded_unknown):
    db = tmp_path / "forged-no-effect.db"
    left, right = work("a", ("K1", "K2")), work("b", ("K2",))
    seed(db, left, right)
    store = SqliteDispatchAdmissionStore(db)
    works = SqliteRecursiveWorkStore(db)
    fingerprint = work_unit_fingerprint(left)
    try:
        first = admit(store, left)
        works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=fingerprint,
            expected_generation=2, status=WorkUnitStatus.RUNNING, lease=first.lease, now=101.)
        if recorded_unknown:
            store.record_result(lineage_id="a", work_fingerprint_value=fingerprint, fencing_token=1,
                result=BackendResult(fingerprint, False, (), ("untrusted-host-claim",), "OUTCOME_UNKNOWN"), recorded_at=102.)
        tables = [row[0] for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {table: store.connection.execute('SELECT * FROM "' + table + '"').fetchall() for table in tables}
        with pytest.raises(ValueError, match="claim-only|NO_EFFECT|no-effect|execution"):
            store.reconcile_admitted(lineage_id="a", work_fingerprint_value=fingerprint, fencing_token=1,
                expected_work_generation=3, lease=first.lease, outcome="NO_EFFECT_CONFIRMED",
                reason="fabricated success says nothing happened", evidence=("public-untrusted-digest",),
                reconciler="forged-host-label", observed_at=103., allow_recorded_outcome_unknown=recorded_unknown)
        assert before == {table: store.connection.execute('SELECT * FROM "' + table + '"').fetchall() for table in tables}
        with pytest.raises(ValueError, match="collision"):
            admit(store, right, now=111.)
    finally:
        works.close()
        store.close()


@pytest.mark.parametrize("source_case", ["executable", "recorded_unknown", "claim_only"])
def test_cold_forged_historical_no_effect_is_not_release_authority(tmp_path, source_case):
    db = tmp_path / "forged-history.db"
    left = replace(work("a"), operation="EXECUTE") if source_case == "executable" else work("a")
    seed(db, left, work("b"))
    store = SqliteDispatchAdmissionStore(db)
    fingerprint = work_unit_fingerprint(left)
    try:
        admit(store, left)
        if source_case == "recorded_unknown":
            store.record_result(lineage_id="a", work_fingerprint_value=fingerprint, fencing_token=1,
                result=BackendResult(fingerprint, False, (), ("untrusted-source",), "OUTCOME_UNKNOWN"), recorded_at=101.)
        digest = _reconciliation_digest(outcome="NO_EFFECT_CONFIRMED", reason="fabricated public statement",
            evidence=("untrusted-host",), reconciler="forged-public-label", observed_at=102.)
        # A hash-valid row is not an authenticated observation. Model historical
        # unsafe input without invoking the now-rejecting current producer API.
        store.connection.execute("INSERT INTO execution_reconciliations (lineage_id,work_fingerprint,fencing_token,sequence,outcome,reason,evidence_json,reconciler,observed_at,reconciliation_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a", fingerprint, 1, 1, "NO_EFFECT_CONFIRMED", "fabricated public statement", '["untrusted-host"]', "forged-public-label", 102., digest))
        store.connection.execute("UPDATE leases SET holder=NULL,expires_at=0")
        store.connection.execute("DELETE FROM collision_reservations")
        store.connection.execute("UPDATE recursive_work_state SET status='FAILED_RETRYABLE',generation=3 WHERE lineage_id='a'")
    finally:
        store.close()
    reopened = SqliteDispatchAdmissionStore(db)
    try:
        unresolved = reopened.unresolved_attempts()
        assert len(unresolved) == 1, "an unauthenticated public NO_EFFECT must remain visibly unresolved"
        assert unresolved[0].phase in {"ADMITTED", "RESULT_RECORDED", "OUTCOME_UNKNOWN"}
    finally:
        reopened.close()
    gate = tmp_path / "gate"
    gate.write_text("GO")
    proc = subprocess.run([sys.executable, "-B", str(Path(__file__)), "worker", str(db), "b", str(gate), str(tmp_path / "ready")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
    assert proc.returncode == 0, proc.stderr
    outcome = json.loads(proc.stdout)
    (tmp_path / "cold-outcome.json").write_text(json.dumps(outcome, indent=2))
    assert outcome["pid"] != os.getpid()
    assert not outcome["ok"], "public forged NO_EFFECT released another fingerprint's collision key"
    assert "collision" in outcome["reason"]


def test_public_claim_only_no_effect_has_no_selected_verifier(tmp_path):
    db = tmp_path / "no-verifier.db"
    seed(db, work("a"), work("b"))
    store = SqliteDispatchAdmissionStore(db)
    try:
        first = admit(store, work("a"))
        with pytest.raises(ValueError, match="verifier|HOLD|NO_EFFECT"):
            store.reconcile_admitted(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(work("a")),
                fencing_token=1, expected_work_generation=2, lease=first.lease,
                outcome="NO_EFFECT_CONFIRMED", reason="untrusted proof string", evidence=("public digest",),
                reconciler="claimed-verifier-label", observed_at=101.)
        with pytest.raises(ValueError, match="collision"):
            admit(store, work("b"), now=111.)
    finally:
        store.close()


class CounterOnlyBackend:
    """A disposable no-effect fixture, never a provider or actual host backend."""
    def __init__(self):
        self.calls = 0

    def execute(self, item):
        self.calls += 1
        return BackendResult(work_unit_fingerprint(item), True, (), ("counter-fixture",))


def test_actual_running_successor_accepts_original_admission(tmp_path):
    db = tmp_path / "operator-running.db"
    item = replace(work("a"), operation="READ_ONLY_FIXTURE", payload={})
    seed(db, item)
    store, works = SqliteDispatchAdmissionStore(db), SqliteRecursiveWorkStore(db)
    try:
        admission = admit(store, item)
        running = works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=work_unit_fingerprint(item),
            expected_generation=admission.work_generation, status=WorkUnitStatus.RUNNING,
            lease=admission.lease, now=101.)
        assert running.generation == admission.work_generation + 1
        backend = CounterOnlyBackend()
        observed = execute_admitted(frontier=None, admission=admission, backend=backend, journal=store, recorded_at=102.)
        assert backend.calls == 1
        assert observed.result.evidence == ("counter-fixture",)
    finally:
        works.close()
        store.close()


@pytest.mark.parametrize("bad_identity", ["budget", "work", "holder", "later_running"])
def test_running_preflight_rejects_unrelated_or_forged_successors(tmp_path, bad_identity):
    db = tmp_path / "operator-forged.db"
    item = replace(work("a"), operation="READ_ONLY_FIXTURE", payload={})
    seed(db, item)
    store, works = SqliteDispatchAdmissionStore(db), SqliteRecursiveWorkStore(db)
    backend = CounterOnlyBackend()
    try:
        admission = admit(store, item)
        fingerprint = work_unit_fingerprint(item)
        if bad_identity == "budget":
            admission = replace(admission, budget_generation=admission.budget_generation + 1)
        elif bad_identity == "work":
            admission = replace(admission, work_generation=admission.work_generation + 1)
        elif bad_identity == "holder":
            admission = replace(admission, lease=replace(admission.lease, holder="forged-label"))
        else:
            generation = admission.work_generation
            for status in (WorkUnitStatus.RUNNING, WorkUnitStatus.OUTCOME_UNKNOWN, WorkUnitStatus.RUNNING):
                generation = works.compare_and_swap_status(lineage_id="a", work_fingerprint_value=fingerprint,
                    expected_generation=generation, status=status, lease=admission.lease, now=101.).generation
        with pytest.raises(ValueError, match="stale|identity|generation|fencing"):
            execute_admitted(frontier=None, admission=admission, backend=backend, journal=store, recorded_at=102.)
        assert backend.calls == 0
        assert store.load_result(lineage_id="a", work_fingerprint_value=fingerprint, fencing_token=1) is None
    finally:
        works.close()
        store.close()


if __name__ == "__main__":
    assert sys.argv[1] == "worker"
    print(json.dumps(worker(*sys.argv[2:])))


@pytest.mark.parametrize("terminal_status", [
    WorkUnitStatus.COMPLETE,
    WorkUnitStatus.FAILED_DETERMINISTIC,
    WorkUnitStatus.SUPERSEDED,
])
def test_legacy_terminal_admitted_attempt_without_reservation_blocks_overlap(
    tmp_path, terminal_status,
):
    """A historical terminal label cannot erase an admitted unresolved effect."""
    db = tmp_path / "historical-terminal-attempt.sqlite"
    a, b = work("a", ("K1", "K2")), work("b", ("K2",))
    seed(db, a, b)
    store = SqliteDispatchAdmissionStore(db)
    try:
        first = admit(store, a)
        a_fp = work_unit_fingerprint(a)
        assert first.lease.fencing_token == 1
        assert store.connection.execute(
            "SELECT COUNT(*) FROM execution_attempts WHERE work_fingerprint=?",
            (a_fp,),
        ).fetchone() == (1,)
        # Reconstitute a historical terminal row with a still unresolved
        # durable attempt and missing old collision reservations. This is
        # disposable legacy evidence, NOT an authorized cancellation.
        store.connection.execute(
            "UPDATE recursive_work_state SET status=? "
            "WHERE lineage_id=? AND work_fingerprint=?",
            (terminal_status.value, a.id, a_fp),
        )
        store.connection.execute(
            "DELETE FROM collision_reservations WHERE work_fingerprint=?",
            (a_fp,),
        )
        store.connection.execute(
            "UPDATE leases SET holder=NULL,expires_at=0 "
            "WHERE work_fingerprint=?",
            (a_fp,),
        )

        def snapshot():
            names = [r[0] for r in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            return {
                n: store.connection.execute(
                    'SELECT * FROM "' + n.replace('"', '""') + '" ORDER BY rowid'
                ).fetchall()
                for n in names
            }

        before = snapshot()
        with pytest.raises(ValueError, match="collision"):
            admit(store, b, holder="new-unverified-owner", now=111.)
        assert snapshot() == before  # No partial lease/key/attempt/budget/work claim.
    finally:
        store.close()


def test_legacy_terminal_orphan_does_not_block_disjoint_key(tmp_path):
    """Fail closed only on overlapping historical keys, not every work item."""
    db = tmp_path / "historical-terminal-disjoint.sqlite"
    a, b = work("a", ("K1", "K2")), work("b", ("K3",))
    seed(db, a, b)
    store = SqliteDispatchAdmissionStore(db)
    try:
        first = admit(store, a)
        a_fp = work_unit_fingerprint(a)
        assert first.lease.fencing_token == 1
        store.connection.execute(
            "UPDATE recursive_work_state SET status='COMPLETE' "
            "WHERE lineage_id=? AND work_fingerprint=?",
            (a.id, a_fp),
        )
        store.connection.execute(
            "DELETE FROM collision_reservations WHERE work_fingerprint=?", (a_fp,),
        )
        store.connection.execute(
            "UPDATE leases SET holder=NULL,expires_at=0 WHERE work_fingerprint=?", (a_fp,),
        )
        assert admit(store, b, holder="disjoint-control", now=111.).lease.holder == "disjoint-control"
    finally:
        store.close()
