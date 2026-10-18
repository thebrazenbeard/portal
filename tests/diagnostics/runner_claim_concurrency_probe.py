"""Disposable, source-bound Runner concurrency probes; never dispatch a backend.

The two collision-contract tests deliberately remain RED: Runner's existing
fingerprint leases do not provide a global collision-key owner, especially across
independent SQLite roots. They are not claimed as defects in a promised global ABI.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time

import pytest

PORTAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PORTAL))

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.leases import Lease
from runner.models import ExactSubject
from runner.persistent_state import SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore, _validate_active_lease_state
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def work(subject="a"):
    return WorkUnit(
        id="disposable-" + subject,
        root_frontier_id="source-concurrency-probe",
        parent_work_id=None,
        inputs=(ExactSubject(repository="disposable/source-fixture", ref="fixture", commit=subject * 40),),
        operation="PORTFOLIO_BOUND_CLAIM",
        required_capabilities=("read",),
        collision_keys=("repository:disposable/shared-target",),
        recursion_depth=0,
        budget_allocation={"children": 0, "active": 1, "retries": 0, "backend_jobs": 1},
        expected_outputs=("read-only-observation",),
        completion_criteria=("qualified-no-effect-receipt",),
        status=WorkUnitStatus.PENDING,
        payload={"fixture": "native-concurrency-api", "execution_authority": False,
                 "protected_effects_authorized": False},
    )


def budget(lineage="lineage-a"):
    return BudgetEnvelope(lineage_id=lineage, max_depth=0, depth=0,
                          remaining_children=0, remaining_active=1,
                          remaining_retries=0, remaining_backend_jobs=1)


def seed(db, subject="a", lineage="lineage-a"):
    store = SqliteDispatchAdmissionStore(db)
    try:
        assert store.initialize_root(budget=budget(lineage), work=work(subject), effective_capabilities=("read",)) == (1, 1)
    finally:
        store.close()


def snapshot(db):
    with closing(sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)) as con:
        return {table: con.execute("SELECT * FROM " + table + " ORDER BY 1, 2").fetchall()
                for table in ("recursive_work_state", "lineage_budgets", "leases",
                              "execution_attempts", "execution_verifications", "execution_reconciliations")}


def child_args(db, subject, lineage, holder, *, mode="admit", now=100.0, ready=None, gate=None):
    return {"db": str(db), "subject": subject, "lineage": lineage, "holder": holder,
            "mode": mode, "now": now, "ready": str(ready) if ready else None,
            "gate": str(gate) if gate else None}


def launch(args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(PORTAL))
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", json.dumps(args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def finish(proc, expected=0):
    stdout, stderr = proc.communicate(timeout=25)
    assert proc.returncode == expected, {"exit": proc.returncode, "stdout": stdout, "stderr": stderr}
    if expected:
        return {"pid": proc.pid, "exit": proc.returncode, "stdout": stdout, "stderr": stderr}
    return json.loads(stdout)


def simultaneous(tmp_path, requests):
    gate = tmp_path / "start-gate"
    procs = []
    ready_paths = []
    for index, request in enumerate(requests):
        ready = tmp_path / f"ready-{index}"
        ready_paths.append(ready)
        procs.append(launch(child_args(**request, ready=ready, gate=gate)))
    try:
        deadline = time.monotonic() + 20
        while not all(path.exists() for path in ready_paths):
            assert time.monotonic() < deadline, "worker readiness timeout"
            assert all(proc.poll() is None for proc in procs), "worker exited before gate"
            time.sleep(0.01)
        gate.write_text("ready", encoding="utf-8")
        results = [finish(proc) for proc in procs]
        assert len({item["pid"] for item in results}) == len(results)
        return results
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)


def evidence(tmp_path, name, details):
    target = tmp_path / (name + ".json")
    target.write_text(json.dumps({"parent_pid": os.getpid(), "python": sys.version,
                                 "os": platform.platform(), "backend_dispatches": 0,
                                 "canonical_memory_writes": 0, **details}, indent=2, sort_keys=True), encoding="utf-8")


def test_same_fingerprint_same_database_has_one_admission(tmp_path):
    db = tmp_path / "same.db"
    seed(db)
    results = simultaneous(tmp_path, [dict(db=db, subject="a", lineage="lineage-a", holder=holder)
                                     for holder in ("fixture-left", "fixture-right")])
    state = snapshot(db)
    evidence(tmp_path, "same-fingerprint", {"results": results, "state": state})
    assert sum(item["ok"] for item in results) == 1
    assert len(state["leases"]) == len(state["execution_attempts"]) == 1
    assert state["lineage_budgets"][0][-1] == state["recursive_work_state"][0][-1] == 2
    assert all("generation mismatch" in item["reason"] for item in results if not item["ok"])


def distinct_collision_case(tmp_path, independent_roots=False):
    db_a = tmp_path / "root-a.db"
    db_b = tmp_path / "root-b.db" if independent_roots else db_a
    seed(db_a, "a", "lineage-a")
    seed(db_b, "b", "lineage-b")
    results = simultaneous(tmp_path, [dict(db=db_a, subject="a", lineage="lineage-a", holder="fixture-left"),
                                     dict(db=db_b, subject="b", lineage="lineage-b", holder="fixture-right")])
    states = {str(db): snapshot(db) for db in {db_a, db_b}}
    evidence(tmp_path, "distinct-fingerprint", {"results": results, "states": states,
              "collision_keys": work("a").collision_keys, "independent_roots": independent_roots,
              "qualification": "NOT a global collision lease or qualified host route"})
    return results


def test_distinct_fingerprints_same_key_expose_local_scope(tmp_path):
    results = distinct_collision_case(tmp_path)
    assert sum(item["ok"] for item in results) == 2
    assert len({item["lease"]["work_fingerprint"] for item in results}) == 2


def test_independent_roots_expose_unshared_lease_scope(tmp_path):
    results = distinct_collision_case(tmp_path, independent_roots=True)
    assert sum(item["ok"] for item in results) == 2


def test_same_fingerprint_independent_roots_have_independent_token_one(tmp_path):
    db_a, db_b = tmp_path / "independent-a.db", tmp_path / "independent-b.db"
    seed(db_a)
    seed(db_b)
    results = simultaneous(tmp_path, [dict(db=db_a, subject="a", lineage="lineage-a", holder="fixture-left"),
                                     dict(db=db_b, subject="a", lineage="lineage-a", holder="fixture-right")])
    evidence(tmp_path, "same-fingerprint-independent-domains", {"results": results,
              "qualification": "two independent SQLite domains do not establish cross-host ownership"})
    assert sum(item["ok"] for item in results) == 2
    assert len({item["lease"]["work_fingerprint"] for item in results}) == 1
    assert all(item["lease"]["fencing_token"] == 1 for item in results)


def test_explicit_collision_contract_same_database_one_owner(tmp_path):
    results = distinct_collision_case(tmp_path)
    assert sum(item["ok"] for item in results) == 1, "UNRESOLVED CONTRACT: fingerprint lease does not reserve shared collision_key"


def test_explicit_collision_contract_independent_roots_one_owner(tmp_path):
    results = distinct_collision_case(tmp_path, independent_roots=True)
    assert sum(item["ok"] for item in results) == 1, "UNRESOLVED CONTRACT: no shared collision authority across independent roots"


def test_interrupted_uncommitted_admission_rolls_back_on_cold_open(tmp_path):
    db = tmp_path / "interrupted.db"
    seed(db)
    before = snapshot(db)
    abrupt = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", mode="interrupt-before-budget")), expected=73)
    after = snapshot(db)
    assert before == after
    resumed = finish(launch(child_args(db, "a", "lineage-a", "fixture-second")))
    final = snapshot(db)
    evidence(tmp_path, "interruption-rollback", {"abrupt": abrupt, "before": before, "after": after,
              "resumed": resumed, "final": final})
    assert resumed["ok"]
    assert len(final["leases"]) == len(final["execution_attempts"]) == 1


def test_committed_abrupt_exit_cold_recovery_no_duplicate_then_expired_refence(tmp_path):
    db = tmp_path / "committed.db"
    seed(db)
    abrupt = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", mode="interrupt-after-commit")), expected=74)
    state_1 = snapshot(db)
    recovered = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", mode="recover", now=101)))
    state_2 = snapshot(db)
    assert recovered["ok"] and recovered["lease"]["fencing_token"] == 1
    assert state_1 == state_2
    foreign_active = finish(launch(child_args(db, "a", "lineage-a", "fixture-other", mode="recover", now=105)))
    assert not foreign_active["ok"] and "active lease" in foreign_active["reason"]
    expired = finish(launch(child_args(db, "a", "lineage-a", "fixture-other", mode="recover", now=111)))
    state_3 = snapshot(db)
    evidence(tmp_path, "cold-recovery-refence", {"abrupt": abrupt, "recovered": recovered,
              "foreign_active": foreign_active, "expired": expired, "initial": state_1,
              "active_replay": state_2, "refenced": state_3})
    assert expired["ok"] and expired["lease"]["fencing_token"] == 2
    assert expired["budget_generation"] == 2 and expired["work_generation"] == 3
    assert len(state_3["execution_attempts"]) == 2
    assert state_3["execution_reconciliations"][0][4] == "NO_EFFECT_CONFIRMED"
    assert all(row[8] is None and row[9] is None for row in state_3["execution_attempts"])


def test_superseded_fence_and_stale_generation_cannot_advance_work(tmp_path):
    db = tmp_path / "stale.db"
    seed(db)
    first = finish(launch(child_args(db, "a", "lineage-a", "fixture-first")))
    newer = finish(launch(child_args(db, "a", "lineage-a", "fixture-other", mode="recover", now=111)))
    assert first["ok"] and newer["ok"]
    before = snapshot(db)
    works = SqliteRecursiveWorkStore(db)
    leases = SqliteLeaseStore(db)
    old_lease, new_lease = Lease(**first["lease"]), Lease(**newer["lease"])
    failures = []
    try:
        for lease, generation, reason in [(old_lease, 3, "stale"), (new_lease, 2, "generation mismatch")]:
            with pytest.raises(ValueError, match=reason) as caught:
                works.compare_and_swap_status(lineage_id="lineage-a", work_fingerprint_value=work_unit_fingerprint(work()),
                    expected_generation=generation, status=WorkUnitStatus.RUNNING, lease=lease, now=112)
            failures.append(str(caught.value))
        assert leases.heartbeat(old_lease, now=112, ttl=10) is None
        assert not leases.release(old_lease, now=112)
        assert not leases.complete(old_lease, now=112)
        _validate_active_lease_state(works.connection, work_fingerprint_value=work_unit_fingerprint(work()), lease=new_lease, now=112)
        with pytest.raises(ValueError, match="expired"):
            _validate_active_lease_state(works.connection, work_fingerprint_value=work_unit_fingerprint(work()), lease=new_lease, now=121)
    finally:
        leases.close()
        works.close()
    after = snapshot(db)
    assert after == before
    evidence(tmp_path, "stale-fence-generation", {"first": first, "newer": newer, "failures": failures,
              "before": before, "after": after})


def test_simultaneous_expired_claim_recovery_has_one_new_fence(tmp_path):
    db = tmp_path / "recovery-race.db"
    seed(db)
    admitted = finish(launch(child_args(db, "a", "lineage-a", "fixture-first")))
    assert admitted["ok"]
    results = simultaneous(tmp_path, [dict(db=db, subject="a", lineage="lineage-a", holder=holder, mode="recover", now=111)
                                     for holder in ("fixture-left", "fixture-right")])
    state = snapshot(db)
    evidence(tmp_path, "concurrent-expired-recovery", {"first": admitted, "results": results, "state": state})
    assert sum(item["ok"] for item in results) == 1
    assert next(item for item in results if item["ok"])["lease"]["fencing_token"] == 2
    assert "active lease" in next(item for item in results if not item["ok"])["reason"]
    assert len(state["execution_attempts"]) == 2
    assert len(state["execution_reconciliations"]) == 1
    assert state["lineage_budgets"][0][-1] == 2
    assert state["recursive_work_state"][0][-1] == 3


@pytest.mark.parametrize("admitted_at", [100, 100.0], ids=["integer-clock", "float-clock"])
def test_initial_admission_numeric_clock_cold_replay(admitted_at, tmp_path):
    db = tmp_path / "numeric-initial.db"
    seed(db)
    first = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", now=admitted_at)))
    replay = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", mode="recover", now=101.0)))
    evidence(tmp_path, "numeric-initial-replay", {"admitted_at_type": type(admitted_at).__name__,
              "first": first, "replay": replay, "state": snapshot(db)})
    assert first["ok"]
    assert replay["ok"], replay.get("reason")
    assert replay["lease"] == first["lease"]


@pytest.mark.parametrize("refenced_at", [111, 111.0], ids=["integer-clock", "float-clock"])
def test_refenced_admission_numeric_clock_cold_replay(refenced_at, tmp_path):
    db = tmp_path / "numeric-refence.db"
    seed(db)
    first = finish(launch(child_args(db, "a", "lineage-a", "fixture-first", now=100.0)))
    refenced = finish(launch(child_args(db, "a", "lineage-a", "fixture-other", mode="recover", now=refenced_at)))
    replay = finish(launch(child_args(db, "a", "lineage-a", "fixture-other", mode="recover", now=112.0)))
    evidence(tmp_path, "numeric-refenced-replay", {"refenced_at_type": type(refenced_at).__name__,
              "first": first, "refenced": refenced, "replay": replay, "state": snapshot(db)})
    assert first["ok"] and refenced["ok"]
    assert replay["ok"], replay.get("reason")
    assert replay["lease"] == refenced["lease"]


def worker(args):
    store = SqliteDispatchAdmissionStore(args["db"])
    result = {"pid": os.getpid(), "mode": args["mode"], "subject": args["subject"], "backend_dispatches": 0}
    try:
        if args.get("ready"):
            Path(args["ready"]).write_text(str(os.getpid()), encoding="utf-8")
        if args.get("gate"):
            deadline = time.monotonic() + 20
            while not Path(args["gate"]).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("start gate timeout")
                time.sleep(0.01)
        if args["mode"] == "interrupt-before-budget":
            def trace(sql):
                if sql.lstrip().startswith("UPDATE lineage_budgets"):
                    print(json.dumps({"pid": os.getpid(), "interrupted": "after lease and attempt INSERT; before budget UPDATE; uncommitted"}), flush=True)
                    os._exit(73)
            store.connection.set_trace_callback(trace)
        if args["mode"] == "recover":
            admitted = store.recover_claim_only_root(budget=budget(args["lineage"]), work=work(args["subject"]),
                effective_capabilities=("read",), holder=args["holder"], now=args["now"], ttl=10)
        else:
            admitted = store.admit(lineage_id=args["lineage"], work_fingerprint_value=work_unit_fingerprint(work(args["subject"])),
                budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
                holder=args["holder"], now=args["now"], ttl=10)
        result.update(ok=True, lease=asdict(admitted.lease), budget_generation=admitted.budget_generation,
                      work_generation=admitted.work_generation)
        if args["mode"] == "interrupt-after-commit":
            print(json.dumps(result), flush=True)
            os._exit(74)
    except ValueError as exc:
        result.update(ok=False, reason=str(exc))
    finally:
        store.close()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "worker":
        raise SystemExit("worker JSON argument required; run main tests through pytest")
    worker(json.loads(sys.argv[2]))
