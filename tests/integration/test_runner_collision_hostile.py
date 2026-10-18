"""Hostile actual-source cases for the single-SQLite-root exclusion boundary.

Public task/actor labels and copied roots remain source integrity evidence, not
authenticated producer/domain authority. No backend or remote route executes.
"""
from contextlib import closing
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore, execute_admitted
from runner.models import ExactSubject
from runner.persistent_state import SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def work(name="a", keys=("shared:literal",), *, executable=False):
    return WorkUnit(id=name, root_frontier_id="hostile-source-fixture", parent_work_id=None,
        inputs=(ExactSubject("fixture/hostile", "source", name * 40),),
        operation="PORTFOLIO_BOUND_CLAIM", required_capabilities=("read",),
        collision_keys=keys, recursion_depth=0,
        budget_allocation={"children": 0, "active": 1, "retries": 1, "backend_jobs": 2},
        expected_outputs=("NO_EFFECT",), completion_criteria=("source-only",),
        status=WorkUnitStatus.PENDING,
        payload={} if executable else {"execution_authority": False, "protected_effects_authorized": False})


def budget(name="a"):
    return BudgetEnvelope(lineage_id=name, max_depth=0, depth=0, remaining_children=0,
        remaining_active=1, remaining_retries=1, remaining_backend_jobs=2)


def seed(store, item):
    store.initialize_root(budget=budget(item.id), work=item, effective_capabilities=("read",))


def admit(store, item, holder="untrusted-holder", now=100.):
    return store.admit(lineage_id=item.id, work_fingerprint_value=work_unit_fingerprint(item),
        budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
        holder=holder, now=now, ttl=10.)


def snapshot(store):
    return {name: store.connection.execute("SELECT * FROM " + name + " ORDER BY 1,2").fetchall()
        for name in ("lineage_budgets", "recursive_work_state", "leases", "execution_attempts")}


@pytest.mark.parametrize("keys", ["abc", {"a"}, {"a": 1}, None, (True,), (None,),
    ("a\nb",), ("a\tb",), ("a\x7fb",), ("\u2003a",), ("a\u00a0",), ("same", "same"),
    ("a\x80b",), ("a\ud800b",)])
def test_nonliteral_or_duplicate_key_contract_rejected_before_root_writes(tmp_path, keys):
    store = SqliteDispatchAdmissionStore(tmp_path / "runner.sqlite")
    try:
        before = snapshot(store)
        with pytest.raises(ValueError, match="collision"):
            seed(store, work(keys=keys))
        assert snapshot(store) == before
    finally:
        store.close()


def test_reordered_multikeys_conflict_and_disjoint_keys_remain_independent(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "runner.sqlite")
    a, b, c = work("a", ("z", "a")), work("b", ("a", "z")), work("c", ("other",))
    try:
        for item in (a, b, c):
            seed(store, item)
        assert work_unit_fingerprint(a) != work_unit_fingerprint(b)
        admit(store, a)
        before = snapshot(store)
        with pytest.raises(ValueError, match="collision"):
            admit(store, b)
        assert snapshot(store) == before
        assert admit(store, c).lease.fencing_token == 1
    finally:
        store.close()


def test_case_prefix_and_unicode_bytes_do_not_silently_alias(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "literal.sqlite")
    keys = ("A", "a", "a:child", "é", "e\u0301")
    try:
        for index, key in enumerate(keys):
            item = work(chr(97 + index), (key,))
            seed(store, item)
            admit(store, item)
        rows = store.connection.execute("SELECT collision_key FROM collision_reservations ORDER BY collision_key").fetchall()
        assert {row[0] for row in rows} == set(keys)
        assert len(rows) == 5
    finally:
        store.close()


def test_three_competing_processes_plus_disjoint_process(tmp_path):
    db = tmp_path / "race.sqlite"
    items = [work("a", ("z", "a")), work("b", ("a", "z")), work("c", ("a",)), work("d", ("other",))]
    store = SqliteDispatchAdmissionStore(db)
    try:
        for item in items:
            seed(store, item)
    finally:
        store.close()
    code = '''import importlib.util,json,os,sys,time
from pathlib import Path
test,db,name,raw,gate,ready=sys.argv[1:]
s=importlib.util.spec_from_file_location('hostile',test);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
store=m.SqliteDispatchAdmissionStore(db)
try:
 Path(ready).write_text(str(os.getpid()))
 deadline=time.monotonic()+20
 while not Path(gate).exists():
  assert time.monotonic()<deadline;time.sleep(.01)
 try:
  result=m.admit(store,m.work(name,tuple(json.loads(raw))),holder=name)
 except ValueError as exc: data={'pid':os.getpid(),'name':name,'admitted':False,'reason':str(exc)}
 else:data={'pid':os.getpid(),'name':name,'admitted':True,'fence':result.lease.fencing_token}
 print(json.dumps(data))
finally:store.close()
'''
    processes = []
    gate = tmp_path / "go"
    try:
        for item in items:
            processes.append(subprocess.Popen([sys.executable, "-B", "-c", code, str(Path(__file__).resolve()),
                str(db), item.id, json.dumps(item.collision_keys), str(gate), str(tmp_path / item.id)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        deadline = time.monotonic() + 20
        while not all((tmp_path / item.id).exists() for item in items):
            assert time.monotonic() < deadline
            time.sleep(.01)
        gate.write_text("GO", encoding="utf-8")
        receipts = []
        for process in processes:
            out, err = process.communicate(timeout=30)
            assert process.returncode == 0, err
            receipts.append(json.loads(out))
        assert len({receipt["pid"] for receipt in receipts}) == 4
        conflicting = [receipt for receipt in receipts if receipt["name"] != "d"]
        assert sum(receipt["admitted"] for receipt in conflicting) == 1
        assert next(receipt for receipt in receipts if receipt["name"] == "d")["admitted"]
        (tmp_path / "multi-process-receipts.json").write_text(json.dumps(receipts, indent=2), encoding="utf-8")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.parametrize("operation", ["release", "complete", "claim"])
def test_raw_lease_api_cannot_bypass_unresolved_durable_attempt(tmp_path, operation):
    path = tmp_path / "runner.sqlite"
    store, leases = SqliteDispatchAdmissionStore(path), SqliteLeaseStore(path)
    item = work()
    try:
        seed(store, item)
        current = admit(store, item)
        before = snapshot(store)
        if operation == "claim":
            result = leases.claim(work_unit_fingerprint(item), holder="replacement", now=111., ttl=10.)
            assert result is None
        else:
            assert getattr(leases, operation)(current.lease, now=101.) is False
        assert snapshot(store) == before
        assert store.unresolved_attempts()[0].phase == "ADMITTED"
    finally:
        leases.close()
        store.close()


def test_expired_unknown_attempt_keeps_conflicting_work_held(tmp_path):
    path = tmp_path / "runner.sqlite"
    store, recursive = SqliteDispatchAdmissionStore(path), SqliteRecursiveWorkStore(path)
    a, b = work(), work("b")
    try:
        seed(store, a)
        seed(store, b)
        admitted = admit(store, a)
        recursive.compare_and_swap_status(lineage_id=a.id, work_fingerprint_value=work_unit_fingerprint(a),
            expected_generation=2, status=WorkUnitStatus.OUTCOME_UNKNOWN, lease=admitted.lease, now=101.)
        before = snapshot(store)
        with pytest.raises(ValueError, match="collision"):
            admit(store, b, now=1000.)
        assert snapshot(store) == before
        assert store.unresolved_attempts()[0].phase == "ADMITTED"
    finally:
        recursive.close()
        store.close()


def test_superseded_exact_fence_cannot_touch_refenced_owner(tmp_path):
    path = tmp_path / "runner.sqlite"
    store, leases, recursive = (SqliteDispatchAdmissionStore(path), SqliteLeaseStore(path),
        SqliteRecursiveWorkStore(path))
    item = work()
    try:
        seed(store, item)
        first = admit(store, item)
        second = store.recover_claim_only_root(budget=budget(), work=item,
            effective_capabilities=("read",), holder="successor", now=111., ttl=10.)
        assert second.lease.fencing_token > first.lease.fencing_token
        before = snapshot(store)
        assert leases.heartbeat(first.lease, now=112., ttl=10.) is None
        assert leases.release(first.lease, now=112.) is False
        assert leases.complete(first.lease, now=112.) is False
        with pytest.raises(ValueError):
            recursive.compare_and_swap_status(lineage_id=item.id,
                work_fingerprint_value=work_unit_fingerprint(item), expected_generation=second.work_generation,
                status=WorkUnitStatus.RUNNING, lease=first.lease, now=112.)
        assert snapshot(store) == before
    finally:
        recursive.close()
        leases.close()
        store.close()


def test_fenced_local_cancellation_keeps_tombstone_generation_and_rejects_former_owner(tmp_path):
    path = tmp_path / "transfer.sqlite"
    store, leases, recursive = (SqliteDispatchAdmissionStore(path), SqliteLeaseStore(path),
        SqliteRecursiveWorkStore(path))
    a, b = work(), work("b")
    try:
        seed(store, a)
        seed(store, b)
        # Existing generic API derives the actual immutable work keys. This
        # reserves local work without creating an execution attempt or calling
        # any backend. Terminal SUPERSEDED cancels this local reservation; it
        # does not manufacture a NO_EFFECT observation for an admitted attempt.
        first = leases.claim(work_unit_fingerprint(a), holder="untrusted-holder", now=100., ttl=10.)
        assert first is not None
        claimed = recursive.compare_and_swap_status(lineage_id=a.id,
            work_fingerprint_value=work_unit_fingerprint(a), expected_generation=1,
            status=WorkUnitStatus.CLAIMED, lease=first, now=100.)
        assert store.connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0
        original = store.connection.execute("SELECT generation FROM collision_reservations").fetchone()[0]
        cancelled = recursive.finalize_terminal_status(lineage_id=a.id,
            work_fingerprint_value=work_unit_fingerprint(a), expected_generation=claimed.generation,
            status=WorkUnitStatus.SUPERSEDED, lease=first, now=101.)
        assert cancelled.work.status is WorkUnitStatus.SUPERSEDED
        assert store.connection.execute("SELECT COUNT(*) FROM execution_reconciliations").fetchone()[0] == 0
        tombstone = store.connection.execute("SELECT work_fingerprint,holder,fencing_token,generation FROM collision_reservations").fetchone()
        assert tombstone == (None, None, None, original)
        successor = admit(store, b, holder="successor", now=102.)
        owner = store.connection.execute("SELECT work_fingerprint,holder,fencing_token,generation FROM collision_reservations").fetchone()
        assert owner[:3] == (work_unit_fingerprint(b), successor.lease.holder, successor.lease.fencing_token)
        assert owner[3] > original
        before = snapshot(store)
        assert leases.heartbeat(first, now=103., ttl=10.) is None
        assert leases.release(first, now=103.) is False
        assert leases.complete(first, now=103.) is False
        with pytest.raises(ValueError):
            recursive.compare_and_swap_status(lineage_id=a.id, work_fingerprint_value=work_unit_fingerprint(a),
                expected_generation=cancelled.generation, status=WorkUnitStatus.RUNNING, lease=first, now=103.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT work_fingerprint,holder,fencing_token,generation FROM collision_reservations").fetchone() == owner
    finally:
        recursive.close()
        leases.close()
        store.close()


@pytest.mark.parametrize("operation", ["competing-admit", "same-owner-recover"])
def test_legacy_missing_reservations_stay_held_without_repinning(tmp_path, operation):
    path = tmp_path / "legacy-shaped.sqlite"
    a, b = work(), work("b")
    store = SqliteDispatchAdmissionStore(path)
    try:
        seed(store, a)
        seed(store, b)
        admit(store, a)
        # Disposable legacy-shaped fixture: old admitted rows existed before the
        # new table. This models that state; no live migration is performed.
        store.connection.execute("DELETE FROM collision_reservations")
    finally:
        store.close()
    cold = SqliteDispatchAdmissionStore(path)
    try:
        before = snapshot(cold)
        with pytest.raises(ValueError, match="collision"):
            if operation == "competing-admit":
                admit(cold, b, now=111.)
            else:
                cold.recover_claim_only_root(budget=budget(), work=a, effective_capabilities=("read",),
                    holder="replacement", now=111., ttl=10.)
        assert snapshot(cold) == before
        assert cold.connection.execute("SELECT COUNT(*) FROM collision_reservations").fetchone()[0] == 0
    finally:
        cold.close()


def test_same_fingerprint_different_lineage_cannot_steal_current_attempt(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "alias.sqlite")
    original = work()
    alias = replace(original, id="b")
    assert work_unit_fingerprint(original) == work_unit_fingerprint(alias)
    try:
        seed(store, original)
        seed(store, alias)
        admit(store, original)
        before = snapshot(store)
        with pytest.raises(ValueError):
            admit(store, alias, holder="alias", now=101.)
        with pytest.raises(ValueError):
            store.recover_claim_only_root(budget=budget("b"), work=alias, effective_capabilities=("read",),
                holder="alias", now=111., ttl=10.)
        assert snapshot(store) == before
    finally:
        store.close()


@pytest.mark.parametrize("field,value", [("holder", None), ("fencing_token", 0), ("generation", 0),
    ("work_fingerprint", None)])
def test_malformed_reservation_tuple_cannot_renew_or_complete(tmp_path, field, value):
    path = tmp_path / "malformed.sqlite"
    store, leases = SqliteDispatchAdmissionStore(path), SqliteLeaseStore(path)
    item = work()
    try:
        seed(store, item)
        current = admit(store, item)
        store.connection.execute("UPDATE collision_reservations SET " + field + "=?", (value,))
        before = snapshot(store)
        malformed = store.connection.execute("SELECT * FROM collision_reservations").fetchall()
        assert leases.heartbeat(current.lease, now=101., ttl=10.) is None
        assert leases.release(current.lease, now=101.) is False
        assert leases.complete(current.lease, now=101.) is False
        with pytest.raises(ValueError, match="collision"):
            store.recover_claim_only_root(budget=budget(), work=item, effective_capabilities=("read",),
                holder="replacement", now=111., ttl=10.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT * FROM collision_reservations").fetchall() == malformed
    finally:
        leases.close()
        store.close()


class ForbiddenBackend:
    def __init__(self):
        self.calls = 0

    def execute(self, _work):
        self.calls += 1
        raise AssertionError("stale admission reached backend boundary")


def test_stale_admission_is_rejected_before_backend_boundary(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "runner.sqlite")
    item = work(executable=True)
    backend = ForbiddenBackend()
    try:
        seed(store, item)
        first = admit(store, item)
        # Caller-authored NO_EFFECT strings cannot authenticate absence of an
        # executable effect. Expiry alone makes this exact admission stale; its
        # reservation must stay owned and no replacement attempt is invented.
        before = snapshot(store)
        with pytest.raises(ValueError):
            execute_admitted(frontier=None, admission=first, backend=backend, journal=store, recorded_at=111.)
        assert backend.calls == 0
        assert snapshot(store) == before
        assert store.connection.execute("SELECT work_fingerprint FROM collision_reservations").fetchone()[0] == work_unit_fingerprint(item)
    finally:
        store.close()


@pytest.mark.parametrize("status", [WorkUnitStatus.CLAIMED, WorkUnitStatus.OUTCOME_UNKNOWN])
def test_public_no_effect_strings_cannot_release_executable_multikey_attempt(tmp_path, status):
    path = tmp_path / "unauthenticated-release.sqlite"
    store, recursive = SqliteDispatchAdmissionStore(path), SqliteRecursiveWorkStore(path)
    a, b = work("a", ("K1", "K2"), executable=True), work("b", ("K2",))
    try:
        seed(store, a)
        seed(store, b)
        current = admit(store, a)
        generation = current.work_generation
        if status is WorkUnitStatus.OUTCOME_UNKNOWN:
            changed = recursive.compare_and_swap_status(lineage_id=a.id,
                work_fingerprint_value=work_unit_fingerprint(a), expected_generation=generation,
                status=status, lease=current.lease, now=101.)
            generation = changed.generation
        before = snapshot(store)
        reservations = store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall()
        with pytest.raises(ValueError):
            store.reconcile_admitted(lineage_id=a.id, work_fingerprint_value=work_unit_fingerprint(a),
                fencing_token=current.lease.fencing_token, expected_work_generation=generation,
                lease=current.lease, outcome="NO_EFFECT_CONFIRMED", reason="no effect, trust me",
                evidence=("PUBLIC_UNVERIFIED_STRING",), reconciler="CLAIMED_VERIFIED_HOST", observed_at=102.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT COUNT(*) FROM execution_reconciliations").fetchone()[0] == 0
        assert store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall() == reservations
        with pytest.raises(ValueError, match="collision"):
            admit(store, b, holder="competing-untrusted-host", now=1000.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall() == reservations
    finally:
        recursive.close()
        store.close()


def test_public_no_effect_strings_cannot_release_claim_only_false_flags(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "false-flags.sqlite")
    a, b = work("a", ("K1", "K2")), work("b", ("K2",))
    try:
        seed(store, a)
        seed(store, b)
        current = admit(store, a)
        before = snapshot(store)
        reservations = store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall()
        with pytest.raises(ValueError):
            store.reconcile_admitted(lineage_id=a.id, work_fingerprint_value=work_unit_fingerprint(a),
                fencing_token=current.lease.fencing_token, expected_work_generation=current.work_generation,
                lease=current.lease, outcome="NO_EFFECT_CONFIRMED", reason="false flags imply no effect, trust me",
                evidence=("PUBLIC_UNVERIFIED_STRING",), reconciler="CLAIMED_VERIFIED_HOST", observed_at=101.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT COUNT(*) FROM execution_reconciliations").fetchone()[0] == 0
        with pytest.raises(ValueError, match="collision"):
            admit(store, b, holder="competing-untrusted-host", now=1000.)
        assert snapshot(store) == before
        assert store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall() == reservations
    finally:
        store.close()


def test_no_effect_payload_is_not_backend_authority_even_for_current_owner(tmp_path):
    store = SqliteDispatchAdmissionStore(tmp_path / "runner.sqlite")
    item = work()
    backend = ForbiddenBackend()
    try:
        seed(store, item)
        current = admit(store, item)
        with pytest.raises(ValueError, match="does not authorize"):
            execute_admitted(frontier=None, admission=current, backend=backend, journal=store, recorded_at=101.)
        assert backend.calls == 0
    finally:
        store.close()


def test_copied_roots_do_not_share_exclusion_authority(tmp_path):
    original, copied = tmp_path / "first.sqlite", tmp_path / "copied.sqlite"
    a, b = work(), work("b")
    store = SqliteDispatchAdmissionStore(original)
    try:
        seed(store, a)
        seed(store, b)
        store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        store.close()
    assert not Path(str(original) + "-wal").exists()
    shutil.copyfile(original, copied)
    one, two = SqliteDispatchAdmissionStore(original), SqliteDispatchAdmissionStore(copied)
    try:
        first, second = admit(one, a), admit(two, b)
        assert first.lease.fencing_token == second.lease.fencing_token == 1
        # An independent copy admits too. Neither public strings nor byte-copy
        # establish globally qualified exclusion; this is an explicit limitation.
        receipt = {"state": "HOLD", "effect": "NO_EFFECT", "domain_authenticated": False,
            "dispatch_authorized": False, "reason": "INDEPENDENT_ROOTS_HAVE_NO_SHARED_EXCLUSION"}
        (tmp_path / "independent-domain-limit.json").write_text(json.dumps(receipt), encoding="utf-8")
    finally:
        one.close()
        two.close()


def proposal_module():
    path = Path(__file__).resolve().parents[1] / "proposals/mono_runner_producer_contract.py"
    spec = importlib.util.spec_from_file_location("collision_hostile_proposal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mutation", ["stale-task", "unrelated-task", "forged-actor", "copied-root"])
def test_real_mono_delegation_preserves_head_scope_but_never_authenticates_producer(tmp_path, mutation):
    pytest.importorskip("vera_core", reason="requires pinned current Mono source")
    from vera_core import QualifiedVeraRuntime, VeraStateDirectory, TaskExecutionError
    from vera_core.local_conversation import open_message
    actor = "PUBLIC_UNTRUSTED_ACTOR"
    root = tmp_path / "mono"
    def runtime(path):
        return QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(path,
            project_id="COLLISION_HOSTILE_SOURCE_FIXTURE", identity_id="UNTRUSTED_SOURCE_FIXTURE"))
    rt = runtime(root)
    intake = open_message(rt.tasks, "literal-conversation", "literal-request", "Exact hostile λ source")
    state = rt.delegate_task_work(intake["task_id"], "read", repository="fixture/hostile", ref="source",
        subject="read-only source", assignee_ref=actor, allowed_effects=(), prohibited_effects=("all effects",),
        return_shape=("source receipt",), evidence_refs=("disposable-source-fixture",))
    ref = state.delegation_ref("read")
    api = proposal_module()
    args = dict(runtime=rt, delegation=ref, actor_ref=actor, expected_task_head=ref.binding_event_digest)
    proposed = api.propose(**args, work=work())
    original = proposed["fingerprint"]
    if mutation == "stale-task":
        rt.tasks.checkpoint(intake["task_id"], "advance", completed_evidence=(), blockers=("HOLD",),
            protected_effects_still_gated=("all",), next_frontier="HOLD",
            lifecycle_evidence_digest=intake["source_digest"], expected_task_head=ref.binding_event_digest)
        assert rt.validate_task_delegation(ref, actor_ref=actor) == ref
        with pytest.raises(ValueError, match="task-scoped"):
            api.revalidate(**args, proposal=proposed)
    elif mutation == "forged-actor":
        with pytest.raises(TaskExecutionError):
            api.revalidate(**{**args, "actor_ref": "CLAIMED_VERIFIED_HOST"}, proposal=proposed)
    else:
        if mutation == "unrelated-task":
            open_message(rt.tasks, "literal-conversation", "other-request", "Independent B")
            assert api.propose(**args, work=work())["fingerprint"] == original
        else:
            assert not list(root.rglob("*-wal")) and not list(root.rglob("*-shm"))
            shutil.copytree(root, tmp_path / "copied-mono")
            args["runtime"] = runtime(tmp_path / "copied-mono")
            assert args["runtime"].validate_task_delegation(ref, actor_ref=actor) == ref
        receipt = api.revalidate(**args, proposal=proposed)
        assert receipt["state"] == "HOLD" and receipt["effect"] == "NO_EFFECT"
        assert receipt["producer_authenticated"] is receipt["dispatch_authorized"] is False
        assert receipt["work_fingerprint"] == original
    assert rt.pc_execution_transport is None and not rt.provider_execution_transports
    assert not rt.tasks.read(intake["task_id"]).closed
    assert rt.tasks.read(intake["task_id"]).packet.subject == "Exact hostile λ source"
