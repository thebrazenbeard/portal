"""Actual API boundary falsifiers; no qualified issuer or effect is constructed.

PASS means the asserted rejection or source limitation was reproduced. These
tests cannot authenticate a database, actor label, producer or collision domain.
"""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

pytest.importorskip("vera_core", reason="requires selected pinned Mono source")
from vera_core import QualifiedVeraRuntime, VeraStateDirectory, TaskExecutionError
from vera_core.local_conversation import open_message
from vera_core.task_execution import TaskDelegationRef
from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint

ACTOR = "UNTRUSTED_DISPOSABLE_D_ACTOR"


def runtime(root):
    return QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(
        root, project_id="DISPOSABLE_D_SOURCE", identity_id="UNTRUSTED_D_FIXTURE"))


def protected_counts(root):
    counts = {}
    for name in ("memory", "effects", "provider", "trust", "pc", "source"):
        for path in sorted((root / name).glob("*.sqlite")):
            assert not Path(str(path) + "-wal").exists()
            assert not Path(str(path) + "-shm").exists()
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                counts[str(path.relative_to(root))] = {
                    table: db.execute('SELECT count(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                    for table in tables}
                assert db.total_changes == 0
    return counts


def delegated(root):
    rt = runtime(root)
    intake = open_message(rt.tasks, "untrusted-d-conversation", "request-a", "literal D λ source")
    state = rt.delegate_task_work(intake["task_id"], "d1",
        repository="thebrazenbeard/project-runner", ref="main", subject="source-only-diagnostic",
        assignee_ref=ACTOR, allowed_effects=(), prohibited_effects=("all protected effects",),
        return_shape=("source receipt",), evidence_refs=("untrusted-fixture:d",))
    return rt, intake, state.delegation_ref("d1")


@pytest.fixture
def actual(tmp_path):
    root = tmp_path / "mono"
    rt, intake, ref = delegated(root)
    assert rt.pc_execution_transport is None
    assert not rt.provider_execution_transports and not rt.source_mutation_transports
    protected_before = protected_counts(root)
    yield rt, intake, ref, root
    assert not rt.tasks.read(intake["task_id"]).closed
    assert protected_counts(root) == protected_before


@pytest.mark.parametrize("field,value", [
    ("task_id", "forged-task"), ("delegation_id", "forged-delegation"),
    ("repository", "forged/repository"), ("ref", "other-ref"),
    ("subject", "other-subject"), ("assignee_ref", "other-actor"),
    ("binding_event_digest", "0" * 64),
])
def test_each_forged_reference_field_rejected_by_real_mono(actual, field, value):
    rt, intake, ref, root = actual
    with pytest.raises((TaskExecutionError, KeyError)):
        rt.validate_task_delegation(replace(ref, **{field: value}), actor_ref=ACTOR)


def test_exact_reference_type_and_explicit_actor_required_for_owner_check(actual):
    rt, intake, ref, root = actual
    with pytest.raises(TaskExecutionError):
        rt.validate_task_delegation(ref.canonical_body(), actor_ref=ACTOR)
    with pytest.raises(TaskExecutionError, match="actor does not own"):
        rt.validate_task_delegation(ref, actor_ref="SUBSTITUTED_ACTOR")
    # API omission is intentionally integrity-only, not proof of actor identity.
    assert rt.validate_task_delegation(ref) == ref


@pytest.mark.parametrize("transition", ["cancel", "reassign", "return"])
def test_inactive_or_superseded_delegation_rejected(actual, transition):
    rt, intake, ref, root = actual
    tid = intake["task_id"]
    if transition == "cancel":
        rt.cancel_task_delegation(tid, "d1", "cancel1", reason="source-only negative",
            evidence_refs=("fixture:cancel",))
    elif transition == "reassign":
        state = rt.reassign_task_delegation(tid, "d1", "reassign1",
            new_assignee_ref="ANOTHER_UNTRUSTED_FIXTURE_ACTOR", evidence_refs=("fixture:reassign",))
        assert state.delegation_ref("d1") != ref
    else:
        rt.return_task_delegation(tid, "d1", "return1", summary="source-only return",
            return_values={"source receipt": "UNAUTHENTICATED_TEST_BYTES"},
            result_evidence_refs=("fixture:return",))
    with pytest.raises(TaskExecutionError):
        rt.validate_task_delegation(ref, actor_ref=ACTOR)


def checkpoint(rt, tid, cid, task_head):
    return rt.tasks.checkpoint(tid, cid, completed_evidence=(), blockers=("HOLD",),
        protected_effects_still_gated=("all effects",), next_frontier="source-only review",
        lifecycle_evidence_digest="a" * 64, expected_task_head=task_head)


def test_active_ref_does_not_replace_latest_task_head_cas(actual):
    rt, intake, ref, root = actual
    tid = intake["task_id"]
    old = rt.tasks.events(tid)[-1].event_digest
    checkpoint(rt, tid, "newer", old)
    # Ref remains active: an adapter that checks only ref validity is insufficient.
    assert rt.validate_task_delegation(ref, actor_ref=ACTOR) == ref
    before = len(rt.tasks.events(tid))
    with pytest.raises(TaskExecutionError, match="task-scoped"):
        checkpoint(rt, tid, "stale-producer", old)
    assert len(rt.tasks.events(tid)) == before


def test_unrelated_task_global_head_does_not_invalidate_local_head(actual):
    rt, intake, ref, root = actual
    tid = intake["task_id"]
    local = rt.tasks.events(tid)[-1].event_digest
    old_global = rt.tasks.read(tid).journal_head_digest
    open_message(rt.tasks, "untrusted-d-conversation", "request-b", "literal B")
    assert rt.tasks.read(tid).journal_head_digest != old_global
    assert rt.tasks.events(tid)[-1].event_digest == local
    assert rt.validate_task_delegation(ref, actor_ref=ACTOR) == ref
    checkpoint(rt, tid, "valid-local-cas", local)


def test_copied_closed_database_reference_validity_is_not_domain_authentication(actual, tmp_path):
    rt, intake, ref, root = actual
    assert not list(root.rglob("*-wal")) and not list(root.rglob("*-shm"))
    copy = tmp_path / "untrusted-copy"
    shutil.copytree(root, copy)
    # A byte-copy preserves public bindings. Domain authentication is missing,
    # rather than created by relabelling a copy as another qualified host root.
    replica = runtime(copy)
    assert replica.validate_task_delegation(ref, actor_ref=ACTOR) == ref
    assert replica.tasks.events(intake["task_id"]) == rt.tasks.events(intake["task_id"])
    assert replica.tasks.events(intake["task_id"])[-1].event_digest == ref.binding_event_digest


def test_cold_process_revalidates_original_ref_without_dispatch(actual, tmp_path):
    rt, intake, ref, root = actual
    script = '''import json,os,sys
from pathlib import Path
from vera_core import QualifiedVeraRuntime,VeraStateDirectory
from vera_core.task_execution import TaskDelegationRef
root,raw,actor=sys.argv[1:]
rt=QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(Path(root),project_id="DISPOSABLE_D_SOURCE",identity_id="UNTRUSTED_D_FIXTURE"))
ref=TaskDelegationRef(**json.loads(raw))
assert rt.validate_task_delegation(ref,actor_ref=actor)==ref
assert rt.pc_execution_transport is None and not rt.provider_execution_transports
assert not rt.tasks.read(ref.task_id).closed
print(json.dumps({"pid":os.getpid(),"task_id":ref.task_id,"binding":ref.binding_event_digest,"events":len(rt.tasks.events(ref.task_id)),"dispatches":0,"state":"HOLD","effect":"NO_EFFECT"}))
'''
    before = len(rt.tasks.events(intake["task_id"]))
    out = subprocess.run([sys.executable, "-B", "-c", script, str(root),
        json.dumps(ref.canonical_body()), ACTOR], check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(out.stdout)
    assert data["pid"] != os.getpid() and data["events"] == before
    assert data["task_id"] == intake["task_id"] and data["binding"] == ref.binding_event_digest
    assert data["dispatches"] == 0 and data["state"] == "HOLD" and data["effect"] == "NO_EFFECT"
    (tmp_path / "cold-receipt.json").write_text(json.dumps(data), encoding="utf-8")


def work(payload):
    return WorkUnit(id="untrusted-work", root_frontier_id="source-only", parent_work_id=None,
        inputs=(ExactSubject("thebrazenbeard/project-runner", "main", "c" * 40),),
        operation="UNAPPROVED_PRODUCER_PROPOSAL", required_capabilities=(),
        collision_keys=("repository:thebrazenbeard/project-runner",), recursion_depth=0,
        budget_allocation={}, expected_outputs=("source receipt",),
        completion_criteria=("NO_EFFECT",), status=WorkUnitStatus.PENDING, payload=payload)


def test_frozen_workunit_does_not_deep_freeze_public_payload():
    supplied = {"association": {"task_id": "task-a", "receipt": {"effect": "NO_EFFECT"}}}
    unit = work(supplied)
    before = work_unit_fingerprint(unit)
    supplied["association"]["task_id"] = "task-b"
    assert work_unit_fingerprint(unit) != before
    assert unit.payload["association"]["task_id"] == "task-b"


def test_detached_json_snapshot_separates_caller_alias_but_not_issuer_trust():
    supplied = {"association": {"task_id": "task-a", "issuer_authenticated": False}}
    snapshot = json.loads(json.dumps(supplied, ensure_ascii=False, allow_nan=False))
    unit = work(snapshot)
    before = work_unit_fingerprint(unit)
    supplied["association"]["task_id"] = "task-b"
    assert work_unit_fingerprint(unit) == before
    # The consumer can still mutate its own dict, so digest must be recomputed at
    # persistence/admission and never cached as proof that a Mapping stayed frozen.
    snapshot["association"]["task_id"] = "task-c"
    assert work_unit_fingerprint(unit) != before
    assert unit.payload["association"]["issuer_authenticated"] is False


@pytest.mark.parametrize("change", ["source", "effect", "actor"])
def test_recomputed_public_receipt_digest_is_not_an_authenticated_signature(change):
    receipt = {"source_digest": "a" * 64, "effect": "NO_EFFECT", "actor": ACTOR,
        "producer_authenticated": False}
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    original = digest(receipt)
    if change == "source": receipt["source_digest"] = "b" * 64
    if change == "effect": receipt["effect"] = "FABRICATED_SUCCESS"
    if change == "actor": receipt["actor"] = "SPOOFED_ACTOR"
    forged = digest(receipt)
    assert forged != original and digest(receipt) == forged
    assert receipt["producer_authenticated"] is False
