"""Crash/cold source fixtures for one shared SQLite collision domain.

These fixtures have no admitted host, model, backend or Mono authority. A local
reservation is exclusion evidence only; it does not authenticate an actor/root.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from runner.budgets import BudgetEnvelope
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.leases import Lease
from runner.models import ExactSubject
from runner.persistent_state import SqliteLeaseStore
from runner.recursive_state import SqliteRecursiveWorkStore
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


def source_work(subject="a", keys=("resource:shared", "resource:second")):
    return WorkUnit(id="cold-source-" + subject, root_frontier_id="collision-cold-fixture",
        parent_work_id=None,
        inputs=(ExactSubject(repository="disposable/collision-source", ref="fixture", commit=subject * 40),),
        operation="PORTFOLIO_BOUND_CLAIM", required_capabilities=("read",),
        collision_keys=tuple(keys), recursion_depth=0,
        budget_allocation={"children": 0, "active": 1, "retries": 0, "backend_jobs": 1},
        expected_outputs=("NO_EFFECT",), completion_criteria=("source-only-reconciliation",),
        status=WorkUnitStatus.PENDING,
        payload={"fixture": "cold-collision-source", "execution_authority": False,
                 "protected_effects_authorized": False})


def source_budget(subject="a"):
    return BudgetEnvelope(lineage_id="cold-lineage-" + subject, max_depth=0, depth=0,
        remaining_children=0, remaining_active=1, remaining_retries=0, remaining_backend_jobs=1)


def seed(path, *subjects):
    store = SqliteDispatchAdmissionStore(path)
    try:
        for subject in subjects:
            assert store.initialize_root(budget=source_budget(subject), work=source_work(subject),
                effective_capabilities=("read",)) == (1, 1)
    finally:
        store.close()


def snapshot(path):
    with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as con:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: con.execute('SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid').fetchall()
                for table in tables}


def request(path, subject="a", holder="untrusted-source-left", mode="admit", now=100.0, cut=None):
    return dict(path=str(path), subject=subject, holder=holder, mode=mode, now=now, cut=cut)


def launch(args):
    # On Windows, venv python.exe can be a redirector. Popen then reports the
    # redirector PID, not the child interpreter PID recorded by the worker.
    # Use the matching base interpreter and expose the venv's dependencies.
    interpreter = sys.executable
    source_paths = [str(ROOT)]
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        import site
        interpreter = getattr(sys, "_base_executable", sys.executable)
        source_paths.extend(site.getsitepackages())
    return subprocess.Popen([interpreter, str(Path(__file__).resolve()), "worker", json.dumps(args)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                 PYTHONPATH=os.pathsep.join(source_paths)),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def finish(process, expected=0):
    try:
        out, err = process.communicate(timeout=25)
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    assert process.returncode == expected, {"pid": process.pid, "returncode": process.returncode,
                                           "stdout": out, "stderr": err, "expected": expected}
    assert out.strip(), {"pid": process.pid, "stderr": err}
    result = json.loads(out.strip().splitlines()[-1])
    assert result["pid"] == process.pid
    return result


def cold(args):
    return finish(launch(args))


def parallel(tmp_path, args):
    gate = tmp_path / "worker-go"
    ready = [tmp_path / f"worker-ready-{index}" for index in range(len(args))]
    processes = [launch(dict(arg, gate=str(gate), ready=str(ready[index]))) for index, arg in enumerate(args)]
    try:
        deadline = time.monotonic() + 20
        while not all(path.exists() for path in ready):
            assert time.monotonic() < deadline, "worker readiness timeout"
            assert all(p.poll() is None for p in processes), "worker exited before gate"
            time.sleep(0.01)
        gate.write_text("go", encoding="utf-8")
        result = [finish(process) for process in processes]
        assert len({item["pid"] for item in result}) == len(result)
        return result
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def evidence(tmp_path, name, **details):
    (tmp_path / (name + ".json")).write_text(json.dumps(dict(parent_pid=os.getpid(),
        python=sys.version, sqlite=sqlite3.sqlite_version, os=platform.platform(),
        backend_dispatches=0, model_calls=0, canonical_memory_writes=0,
        authority="unqualified source fixture; same SQLite root only", **details), indent=2, sort_keys=True), encoding="utf-8")


def test_cold_distinct_fingerprints_one_shared_collision_owner(tmp_path):
    path = tmp_path / "shared.db"
    seed(path, "a", "b")
    results = parallel(tmp_path, [request(path, "a"), request(path, "b", holder="untrusted-source-right")])
    state = snapshot(path)
    assert sum(result["ok"] for result in results) == 1, "different WorkUnit fingerprints must share collision-key exclusion"
    assert len(state["execution_attempts"]) == 1
    assert sorted(row[-1] for row in state["recursive_work_state"]) == [1, 2]
    assert sorted(row[-1] for row in state["lineage_budgets"]) == [1, 2]
    winner = next(result for result in results if result["ok"])
    replay = cold(request(path, winner["subject"], winner["holder"], mode="recover", now=101))
    assert replay["ok"] and replay["lease"] == winner["lease"]
    assert snapshot(path) == state
    evidence(tmp_path, "competing-cold-claims", results=results, state=state, replay=replay)


@pytest.mark.parametrize("cut", ["partial-reservations", "before-commit"])
def test_crash_before_commit_rolls_back_all_reservations_and_budget(tmp_path, cut):
    path = tmp_path / "rollback.db"
    seed(path, "a", "b")
    before = snapshot(path)
    interrupted = finish(launch(request(path, cut=cut)), expected=73)
    assert len(interrupted["reservation_rows"]) == (1 if cut == "partial-reservations" else 2)
    after = snapshot(path)
    assert after == before, "crash must roll back partial keys, lease, attempt, budget and work generation together"
    other = cold(request(path, "b", holder="untrusted-source-right"))
    evidence(tmp_path, "rollback-" + cut, before=before, interrupted=interrupted, after=after, other=other)
    assert other["ok"]


def test_crash_after_commit_cold_replay_preserves_original_attempt(tmp_path):
    path = tmp_path / "committed.db"
    seed(path, "a", "b")
    original_source = hashlib.sha256(repr(source_work()).encode()).hexdigest()
    interrupted = finish(launch(request(path, cut="after-commit")), expected=74)
    committed = snapshot(path)
    assert len(committed["collision_reservations"]) == 2
    assert all(row[-1] == 1 and row[3] == 1 for row in committed["collision_reservations"])
    replay = cold(request(path, mode="recover", now=101))
    duplicate = cold(request(path, "b", holder="untrusted-source-right", now=102))
    after = snapshot(path)
    evidence(tmp_path, "committed-lost-response", original_source_sha256=original_source,
        interrupted=interrupted, committed=committed, replay=replay, duplicate=duplicate, after=after)
    assert replay["ok"] and replay["lease"]["fencing_token"] == 1
    assert replay["budget_generation"] == replay["work_generation"] == 2
    assert not duplicate["ok"], "lost response must not free the committed shared collision domain"
    assert committed == after
    assert hashlib.sha256(repr(source_work()).encode()).hexdigest() == original_source
    assert len(after["execution_attempts"]) == 1
    assert not after["execution_verifications"] and not after["execution_reconciliations"]


def test_claim_only_expired_refence_and_stale_tokens_are_cold_fenced(tmp_path):
    path = tmp_path / "refenced.db"
    seed(path, "a", "b")
    first = cold(request(path))
    initial = snapshot(path)
    assert len(initial["collision_reservations"]) == 2
    assert all(row[-1] == 1 and row[3] == 1 for row in initial["collision_reservations"])
    newer = cold(request(path, holder="untrusted-source-next", mode="recover", now=111))
    assert first["ok"] and newer["ok"]
    assert newer["lease"]["fencing_token"] == 2
    assert newer["budget_generation"] == 2 and newer["work_generation"] == 3
    before = snapshot(path)
    assert all(row[-1] == 2 and row[3] == 2 and row[2] == "untrusted-source-next"
               for row in before["collision_reservations"])
    stale = cold(dict(request(path, now=112), mode="stale", lease=first["lease"]))
    competitor = cold(request(path, "b", holder="untrusted-source-right", now=112))
    replay = cold(request(path, holder="untrusted-source-next", mode="recover", now=112))
    after = snapshot(path)
    evidence(tmp_path, "refence-and-stale-operations", first=first, newer=newer, stale=stale,
        competitor=competitor, replay=replay, before=before, after=after)
    assert stale["operations"] == {"heartbeat": False, "release": False, "complete": False, "work_advance": False}
    assert not competitor["ok"]
    assert replay["ok"] and replay["lease"] == newer["lease"]
    assert before == after
    assert len(after["execution_attempts"]) == 2
    assert len(after["execution_reconciliations"]) == 1
    assert after["execution_reconciliations"][0][4] == "NO_EFFECT_CONFIRMED"


def test_crash_during_expired_refence_rolls_back_original_domain(tmp_path):
    path = tmp_path / "refence-rollback.db"
    seed(path, "a", "b")
    initial = cold(request(path))
    assert initial["ok"]
    before = snapshot(path)
    interrupted = finish(launch(request(path, holder="untrusted-source-next", mode="recover", now=111, cut="before-commit")), expected=73)
    assert snapshot(path) == before
    competitor = cold(request(path, "b", holder="untrusted-source-right", now=112))
    resumed = cold(request(path, holder="untrusted-source-next", mode="recover", now=112))
    evidence(tmp_path, "refence-rollback", initial=initial, interrupted=interrupted, before=before,
        competitor=competitor, resumed=resumed, after=snapshot(path))
    assert not competitor["ok"]
    assert resumed["ok"] and resumed["lease"]["fencing_token"] == 2
    assert len(snapshot(path)["execution_reconciliations"]) == 1


def test_crash_after_refence_commit_cold_replay_keeps_reservation_generation(tmp_path):
    path = tmp_path / "refence-lost-response.db"
    seed(path, "a", "b")
    initial = cold(request(path))
    assert initial["ok"]
    interrupted = finish(launch(request(path, holder="untrusted-source-next", mode="recover",
        now=111, cut="after-commit")), expected=74)
    committed = snapshot(path)
    replay = cold(request(path, holder="untrusted-source-next", mode="recover", now=112))
    competitor = cold(request(path, "b", holder="untrusted-source-right", now=112))
    evidence(tmp_path, "refence-committed-lost-response", initial=initial, interrupted=interrupted,
        committed=committed, replay=replay, competitor=competitor, after=snapshot(path))
    assert replay["ok"] and replay["lease"]["fencing_token"] == 2
    assert replay["budget_generation"] == 2 and replay["work_generation"] == 3
    assert all(row[-1] == 2 and row[3] == 2 for row in committed["collision_reservations"])
    assert not competitor["ok"]
    assert snapshot(path) == committed
    assert len(committed["execution_attempts"]) == 2
    assert len(committed["execution_reconciliations"]) == 1


def test_simultaneous_cold_refence_has_one_new_owner(tmp_path):
    path = tmp_path / "recover-race.db"
    seed(path, "a", "b")
    initial = cold(request(path))
    assert initial["ok"]
    results = parallel(tmp_path, [request(path, holder="untrusted-source-next", mode="recover", now=111),
        request(path, holder="untrusted-source-other", mode="recover", now=111)])
    assert sum(result["ok"] for result in results) == 1
    winner = next(result for result in results if result["ok"])
    assert winner["lease"]["fencing_token"] == 2
    competitor = cold(request(path, "b", holder="untrusted-source-right", now=112))
    state = snapshot(path)
    evidence(tmp_path, "simultaneous-cold-refence", initial=initial, results=results, competitor=competitor, state=state)
    assert not competitor["ok"]
    assert len(state["execution_attempts"]) == 2
    assert len(state["execution_reconciliations"]) == 1


def test_unknown_attempt_retains_collision_after_expiry_and_cold_restart(tmp_path):
    path = tmp_path / "unknown.db"
    seed(path, "a", "b")
    first = cold(request(path))
    assert first["ok"]
    works = SqliteRecursiveWorkStore(path)
    try:
        works.compare_and_swap_status(lineage_id=source_budget().lineage_id,
            work_fingerprint_value=work_unit_fingerprint(source_work()), expected_generation=2,
            status=WorkUnitStatus.OUTCOME_UNKNOWN, lease=Lease(**first["lease"]), now=101)
    finally:
        works.close()
    before = snapshot(path)
    recovered = cold(request(path, holder="untrusted-source-next", mode="recover", now=111))
    competitor = cold(request(path, "b", holder="untrusted-source-right", now=111))
    after = snapshot(path)
    evidence(tmp_path, "unknown-hold", first=first, recovered=recovered, competitor=competitor, before=before, after=after)
    assert not recovered["ok"] and "OUTCOME_UNKNOWN" in recovered["reason"]
    assert not competitor["ok"], "lease expiry is not proof that an unknown attempt had no effects"
    assert before == after
    assert not after["execution_reconciliations"]


def worker(args):
    store = SqliteDispatchAdmissionStore(args["path"])
    result = dict(pid=os.getpid(), subject=args["subject"], holder=args["holder"],
                  backend_dispatches=0, model_calls=0, canonical_memory_writes=0)
    try:
        if args.get("ready"):
            Path(args["ready"]).write_text(str(os.getpid()), encoding="utf-8")
        if args.get("gate"):
            deadline = time.monotonic() + 20
            while not Path(args["gate"]).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("gate timeout")
                time.sleep(0.01)
        cut = args.get("cut")
        reservation_statements = 0
        if cut in {"partial-reservations", "before-commit"}:
            def trace(sql):
                nonlocal reservation_statements
                normalized = sql.strip().lower()
                reservation_write = normalized.startswith(("insert", "update")) and ("collision" in normalized or "reservation" in normalized)
                if reservation_write:
                    reservation_statements += 1
                reached = (cut == "partial-reservations" and reservation_statements == 2 and reservation_write) or (cut == "before-commit" and normalized == "commit")
                if reached:
                    rows = store.connection.execute("SELECT * FROM collision_reservations ORDER BY collision_key").fetchall()
                    print(json.dumps(dict(result, cut=cut, sql=sql, reservation_statements=reservation_statements,
                                          reservation_rows=rows)), flush=True)
                    os._exit(73)
            store.connection.set_trace_callback(trace)
        if args["mode"] == "stale":
            old = Lease(**args["lease"])
            leases = SqliteLeaseStore(args["path"])
            works = SqliteRecursiveWorkStore(args["path"])
            try:
                operations = dict(heartbeat=leases.heartbeat(old, now=args["now"], ttl=10) is not None,
                    release=leases.release(old, now=args["now"]), complete=leases.complete(old, now=args["now"]))
                try:
                    works.compare_and_swap_status(lineage_id=source_budget(args["subject"]).lineage_id,
                        work_fingerprint_value=old.work_fingerprint, expected_generation=3,
                        status=WorkUnitStatus.RUNNING, lease=old, now=args["now"])
                    operations["work_advance"] = True
                except ValueError:
                    operations["work_advance"] = False
                result.update(ok=True, operations=operations)
            finally:
                works.close()
                leases.close()
        else:
            if args["mode"] == "recover":
                admitted = store.recover_claim_only_root(budget=source_budget(args["subject"]),
                    work=source_work(args["subject"]), effective_capabilities=("read",),
                    holder=args["holder"], now=args["now"], ttl=10)
            else:
                admitted = store.admit(lineage_id=source_budget(args["subject"]).lineage_id,
                    work_fingerprint_value=work_unit_fingerprint(source_work(args["subject"])),
                    budget_scope_id="root", expected_budget_generation=1, expected_work_generation=1,
                    holder=args["holder"], now=args["now"], ttl=10)
            result.update(ok=True, lease=asdict(admitted.lease), budget_generation=admitted.budget_generation,
                          work_generation=admitted.work_generation)
            if cut == "after-commit":
                print(json.dumps(dict(pid=os.getpid(), cut="after-commit-before-response", admission_response_delivered=False)), flush=True)
                os._exit(74)
    except ValueError as exc:
        result.update(ok=False, reason=str(exc))
    finally:
        store.close()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "worker":
        raise SystemExit("worker JSON argument required")
    worker(json.loads(sys.argv[2]))
