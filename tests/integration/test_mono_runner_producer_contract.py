"""Executable owner proposal, not an approved producer or admitted WorkUnit."""
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import pytest

pytest.importorskip("vera_core", reason="requires selected pinned Mono source")
from vera_core import QualifiedVeraRuntime, VeraStateDirectory, TaskExecutionError
from vera_core.local_conversation import open_message
from runner.models import ExactSubject
from runner.work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint

ACTOR = "EXPLICIT_UNTRUSTED_SOURCE_FIXTURE"
PATH = Path(__file__).resolve().parents[1] / "proposals/mono_runner_producer_contract.py"


def contract():
    assert PATH.is_file(), "EXECUTABLE_VALIDATED_DELEGATION_PRODUCER_PROPOSAL_MISSING"
    spec = importlib.util.spec_from_file_location("producer_proposal", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    rt = QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(tmp_path / "mono",
        project_id="SOURCE_PROPOSAL_FIXTURE", identity_id="UNTRUSTED_FIXTURE"))
    intake = open_message(rt.tasks, "untrusted-proposal", "original-request", "Exact λ producer source")
    delegated = rt.delegate_task_work(intake["task_id"], "read-source",
        repository="thebrazenbeard/project-runner", ref="main", subject="read-only-source",
        assignee_ref=ACTOR, allowed_effects=(), prohibited_effects=("all protected effects",),
        return_shape=("source receipt",), evidence_refs=("test-only:proposal",))
    ref = delegated.delegation_ref("read-source")
    nested = {"execution_authority": False, "protected_effects_authorized": False, "nested": {"original": "value"}}
    work = WorkUnit(id="test-only", root_frontier_id="test-only", parent_work_id=None,
        inputs=(ExactSubject(repository=ref.repository, ref=ref.ref, commit="a" * 40),),
        operation="PORTFOLIO_BOUND_CLAIM", required_capabilities=("read",),
        collision_keys=("repository:" + ref.repository,), recursion_depth=0,
        budget_allocation={"children": 0, "active": 1, "retries": 0, "backend_jobs": 1},
        expected_outputs=("source receipt",), completion_criteria=("NO_EFFECT",),
        status=WorkUnitStatus.PENDING, payload=nested)
    return rt, intake, ref, work


def build(source):
    rt, intake, ref, work = source
    return contract().propose(runtime=rt, delegation=ref, actor_ref=ACTOR,
        expected_task_head=ref.binding_event_digest, work=work)


def check(source, proposal, **changes):
    rt, intake, ref, work = source
    args = dict(runtime=rt, delegation=ref, actor_ref=ACTOR,
        expected_task_head=ref.binding_event_digest, proposal=proposal)
    args.update(changes)
    return contract().revalidate(**args)


def test_seals_actual_delegation_and_original_task_before_any_admission(source):
    rt, intake, ref, original = source
    proposal = build(source)
    association = proposal["association"]
    assert association["delegation"] == ref.canonical_body()
    assert association["task_packet_digest"] == intake["packet_digest"]
    assert association["opened_lifecycle_evidence_digest"] == intake["source_digest"]
    assert association["original_text"] == intake["text"]
    assert association["opened_head"] == intake["opened_head"]
    assert association["expected_task_head"] == ref.binding_event_digest != intake["opened_head"]
    assert work_unit_fingerprint(proposal["work"]) == proposal["fingerprint"] != work_unit_fingerprint(original)
    receipt = check(source, proposal)
    assert receipt["state"] == "HOLD" and receipt["effect"] == "NO_EFFECT"
    assert receipt["producer_authenticated"] is receipt["dispatch_authorized"] is False
    assert "UNAPPROVED_PRODUCER_ABI" in receipt["reasons"]
    assert "CLAIM_COLLISION_DOMAIN_UNQUALIFIED" in receipt["reasons"]
    assert len(rt.tasks.events(intake["task_id"])) == 2
    assert rt.pc_execution_transport is None and not rt.provider_execution_transports


def test_nested_input_detached_but_mutated_proposal_rejected(source):
    proposal = build(source)
    source[3].payload["nested"]["original"] = "caller changed"
    assert work_unit_fingerprint(proposal["work"]) == proposal["fingerprint"]
    proposal["work"].payload["nested"]["original"] = "consumer changed"
    with pytest.raises(ValueError, match="fingerprint"):
        check(source, proposal)


def test_unrelated_global_advance_does_not_change_task_bound_work_identity(source):
    first = build(source)
    open_message(source[0].tasks, "untrusted-proposal", "other-task", "Independent B")
    second = build(source)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["global_head_observed"] != second["global_head_observed"]
    assert check(source, first)["state"] == "HOLD"


def test_same_task_advance_rejects_even_when_reference_is_still_active(source):
    proposal = build(source)
    rt, intake, ref, work = source
    rt.tasks.checkpoint(intake["task_id"], "advance", completed_evidence=(), blockers=("HOLD",),
        protected_effects_still_gated=("all",), next_frontier="HOLD",
        lifecycle_evidence_digest=intake["source_digest"], expected_task_head=ref.binding_event_digest)
    assert rt.validate_task_delegation(ref, actor_ref=ACTOR) == ref
    with pytest.raises(ValueError, match="task-scoped"):
        check(source, proposal)


def test_explicit_actor_and_exact_target_scope_cannot_be_substituted(source):
    with pytest.raises((ValueError, TaskExecutionError)):
        check(source, build(source), actor_ref=None)
    bad = (*source[:3], replace(source[3], inputs=(ExactSubject(repository="other/repo", ref="main", commit="a" * 40),)))
    with pytest.raises(ValueError, match="scope"):
        build(bad)


def test_cancellation_invalidates_producer_snapshot(source):
    proposal = build(source)
    rt, intake, ref, work = source
    rt.cancel_task_delegation(intake["task_id"], "read-source", "cancel", reason="source-only negative", evidence_refs=("fixture",))
    with pytest.raises(TaskExecutionError, match="active"):
        check(source, proposal)


@pytest.mark.parametrize("advance", ["same-task", "unrelated-task"])
def test_committed_writer_between_proposal_and_admission_boundary_holds(source, advance, tmp_path):
    rt, intake, ref, work = source
    proposal = build(source)
    ready, commit = threading.Event(), threading.Event()
    errors = []
    def writer():
        try:
            ready.set()
            assert commit.wait(10)
            if advance == "same-task":
                with rt.tasks.action_lock():
                    rt.tasks.checkpoint(intake["task_id"], "barrier-advance", completed_evidence=(),
                        blockers=("HOLD",), protected_effects_still_gated=("all",), next_frontier="HOLD",
                        lifecycle_evidence_digest=intake["source_digest"], expected_task_head=ref.binding_event_digest)
            else:
                open_message(rt.tasks, "untrusted-proposal", "barrier-B", "Unrelated B")
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=writer)
    thread.start()
    assert ready.wait(10)
    commit.set()
    thread.join(10)
    assert not thread.is_alive() and not errors
    # This proposal never invokes Runner admission. It cannot turn a precheck
    # into two-store atomicity; same-task change is rejected before any consumer.
    if advance == "unrelated-task":
        assert check(source, proposal)["state"] == "HOLD"
        assert build(source)["fingerprint"] == proposal["fingerprint"]
        return
    assert rt.validate_task_delegation(ref, actor_ref=ACTOR) == ref
    with pytest.raises(ValueError, match="task-scoped"):
        check(source, proposal)
    code = '''
import importlib.util,json,sys
from pathlib import Path
from vera_core import QualifiedVeraRuntime,VeraStateDirectory
from vera_core.task_execution import TaskDelegationRef
from runner.recursive_state import _work_from_payload
from runner.work_units import WorkUnitStatus
root,helper,raw=sys.argv[1:]; data=json.loads(raw)
rt=QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(Path(root),project_id="SOURCE_PROPOSAL_FIXTURE",identity_id="UNTRUSTED_FIXTURE"))
s=importlib.util.spec_from_file_location('proposal',helper); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
data['proposal']['work']=_work_from_payload(data['proposal']['work'],status=WorkUnitStatus.PENDING)
try:m.revalidate(runtime=rt,delegation=TaskDelegationRef(**data['ref']),actor_ref=data['actor'],expected_task_head=data['head'],proposal=data['proposal'])
except ValueError as e:
 assert 'task-scoped' in str(e); print(json.dumps({'state':'HOLD','task_id':data['ref']['task_id'],'dispatches':0,'reason':str(e)}))
else:raise AssertionError('stale source proposal was not rejected')
'''
    from runner.recursive_state import _work_payload
    data = {"ref": ref.canonical_body(), "actor": ACTOR, "head": ref.binding_event_digest,
        "proposal": {**proposal, "work": _work_payload(proposal["work"])}}
    cold = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path / "mono"), str(PATH), json.dumps(data)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=20)
    assert cold.returncode == 0, cold.stderr
    assert json.loads(cold.stdout)["state"] == "HOLD"
