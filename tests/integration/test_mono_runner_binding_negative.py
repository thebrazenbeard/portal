"""Disposable actual API composition; no selected/live claim or positive authority.

The diagnostic is deliberately outside the production packages. These tests do
not supply the missing owner-approved immutable Mono/WorkUnit association.
"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict
import hashlib
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

pytest.importorskip("vera_core", reason="requires explicitly pinned Mono source")
from vera_core.local_conversation import ConversationError, open_message
from vera_core.portal_task_handoff import export_portal_route_request, verify_portal_route_assessment
from vera_core.task_execution import TaskExecutionLedger
from portal import PortalRouteAdvertisement
from portal.vera_task_handoff import assess_vera_task_route
from runner.cli import main
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.durable_dispatch import SqliteDispatchAdmissionStore

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC = ROOT / "tests/diagnostics/mono_runner_binding_probe.py"
LITERAL = "Read-only diagnostic λ source"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def closed_db(path, *, readonly=False):
    # Only already-closed disposable fixtures; do not create WAL/SHM on reads.
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) if readonly else sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def diagnostic():
    assert DIAGNOSTIC.is_file(), (
        "DIAGNOSTIC_ENTRYPOINT_MISSING: real task/claim evidence has no composed read-only diagnostic"
    )
    spec = importlib.util.spec_from_file_location("mono_runner_binding_probe", DIAGNOSTIC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.diagnose


@pytest.fixture(scope="module")
def plan_text():
    out = StringIO()
    with redirect_stdout(out):
        assert main(["portfolio-wave-plan", "--max-parallel", "50", "--max-per-identity", "50", "--max-per-family", "50"]) == 0
    return out.getvalue()


@pytest.fixture
def evidence(tmp_path, plan_text):
    ledger = TaskExecutionLedger(tmp_path / "mono/tasks.sqlite3")
    intake = open_message(ledger, "diagnostic-conversation", "original-request", LITERAL)
    plan = tmp_path / "plan.json"
    plan.write_text(plan_text, encoding="utf-8")
    class OfflineSource:
        """Visible fixed test source, never an authenticated target observation."""
        def __init__(self):
            self.reads = []
        def read_ref(self, repository, ref):
            self.reads.append((repository, ref))
            assert (repository, ref) == ("thebrazenbeard/project-runner", "main")
            assert len(ledger.events(intake["task_id"])) == 1
            return "a" * 40
        def read_file(self, *args):
            return None
        def create_branch(self, *args):
            raise AssertionError("no source effect")
        def put_file(self, *args, **kwargs):
            raise AssertionError("no source effect")
    transport = OfflineSource()
    db = tmp_path / "runner.sqlite3"
    claim = claim_bound_plan_subject(
        plan_path=plan, wave_path=ROOT / "portfolio/advancement_wave.public.json",
        corpus_path=ROOT / "portfolio/corpus.public.json", projects_path=ROOT / "registry/projects.yaml",
        subject_id="project-runner", state_db=db, holder="DISPOSABLE_DIAGNOSTIC_FIXTURE",
        lease_ttl=60, allowed_repositories=("thebrazenbeard/project-runner",),
        transport=transport, clock=lambda: 1000,
    )
    request = export_portal_route_request(ledger, intake["task_id"], node_id="fixture-host",
        target_kind="repository", target_id=claim.repository, required_capabilities=("source_read",))
    route = PortalRouteAdvertisement(adapter_id="fixture-only", route_id="read-only",
        node_id="fixture-host", target_kind="repository", target_id=claim.repository,
        capabilities=("source_read",), effect_capabilities=(), authorized_effects=(),
        available=True, attached=True, current=True)
    advice = assess_vera_task_route(request, (route,))
    assert advice["state"] == "HELD" and advice["route"] is None
    assert transport.reads == [(claim.repository, claim.ref)]
    return dict(ledger=ledger, intake=intake, request=request, assessment=advice,
        runner_db=db, expected_runner_db=db, claim=asdict(claim), now=1001.0,
        transport=transport)


def run(evidence, **changes):
    args = {k: v for k, v in evidence.items() if k != "transport"}
    args.update(changes)
    return diagnostic()(**args)


def held(report, reason=None):
    assert report["state"] == "HOLD" and report["effect"] == "NO_EFFECT"
    assert report["dispatch_authorized"] is report["producer_authenticated"] is False
    assert report["model_calls"] == report["dispatches"] == report["runner_writes"] == 0
    assert report["reasons"]
    if reason:
        assert reason in report["reasons"], report


def unchanged(e):
    assert len(e["ledger"].events(e["intake"]["task_id"])) == 1
    assert not e["ledger"].read(e["intake"]["task_id"]).closed
    with closed_db(e["runner_db"], readonly=True) as db:
        assert db.execute("SELECT count(*),count(result_json) FROM execution_attempts").fetchone() == (1, 0)
    assert len(e["transport"].reads) == 1


def test_valid_fixture_lease_still_has_no_canonical_producer_correlation(evidence):
    report = run(evidence)
    held(report, "MONO_WORKUNIT_PRODUCER_ABI_MISSING")
    assert "CLAIM_EVIDENCE_INVALID" not in report["reasons"]
    assert "WORK_INTEGRITY_INVALID" not in report["reasons"]
    assert report["lease_checks"] == "VALID_AT_FIXTURE_CLOCK_ONLY"
    assert report["task_head"] == evidence["intake"]["opened_head"]
    assert report["task_id"] == evidence["intake"]["task_id"]
    unchanged(evidence)


@pytest.mark.parametrize("case", ["missing_db", "missing_claim", "expired", "forged_holder", "forged_fence", "forged_expiry", "wrong_lineage", "wrong_fingerprint", "wrong_repository", "wrong_ref", "wrong_source_head", "stale_generation", "wrong_domain"])
def test_claim_negatives_are_hold_without_repair(evidence, case, tmp_path):
    claim = dict(evidence["claim"])
    changes = {"claim": claim}
    reason = "CLAIM_EVIDENCE_INVALID"
    if case == "missing_db":
        changes["runner_db"] = tmp_path / "absent.sqlite3"
        changes["expected_runner_db"] = changes["runner_db"]
        reason = "RUNNER_DB_UNAVAILABLE"
    elif case == "missing_claim":
        changes["claim"] = None
        reason = "CLAIM_MISSING"
    elif case == "expired":
        changes["now"] = 1060.0
        reason = "LEASE_INVALID"
    elif case == "wrong_domain":
        copied = tmp_path / "other-domain.sqlite3"
        copied.write_bytes(evidence["runner_db"].read_bytes())
        changes["runner_db"] = copied
        reason = "CLAIM_DOMAIN_MISMATCH"
    else:
        key, value = {
            "forged_holder": ("holder", "ATTACKER"), "forged_fence": ("fencing_token", 2),
            "forged_expiry": ("lease_expires_at", 9999999999.0),
            "wrong_lineage": ("lineage_id", "other-lineage"), "wrong_fingerprint": ("work_fingerprint", "b" * 64),
            "wrong_repository": ("repository", "thebrazenbeard/portal"), "wrong_ref": ("ref", "other"),
            "wrong_source_head": ("exact_head", "b" * 40), "stale_generation": ("work_generation", 1),
        }[case]
        claim[key] = value
        if case in {"forged_holder", "forged_fence"}:
            reason = "LEASE_INVALID"
    before = sha(evidence["runner_db"])
    held(run(evidence, **changes), reason)
    assert sha(evidence["runner_db"]) == before
    assert not (tmp_path / "absent.sqlite3").exists()
    unchanged(evidence)


@pytest.mark.parametrize("column,value,reason", [
    ("completed", 1, "LEASE_INVALID"), ("fencing_token", 2, "LEASE_INVALID"),
    ("expires_at", 1000.0, "LEASE_INVALID"),
])
def test_adversarial_superseded_lease_rows_are_not_renewed(evidence, column, value, reason):
    # Explicit corrupt disposable rows are hostile data, not genuine admission.
    with closed_db(evidence["runner_db"]) as db:
        db.execute(f"UPDATE leases SET {column}=?", (value,))
    before = sha(evidence["runner_db"])
    held(run(evidence), reason)
    assert sha(evidence["runner_db"]) == before


def test_caller_inserted_mono_binding_cannot_authenticate_work(evidence):
    with closed_db(evidence["runner_db"]) as db:
        payload = json.loads(db.execute("SELECT work_json FROM recursive_work_state").fetchone()[0])
        payload["payload"]["mono_task_id"] = evidence["intake"]["task_id"]
        db.execute("UPDATE recursive_work_state SET work_json=?", (json.dumps(payload),))
    held(run(evidence), "WORK_INTEGRITY_INVALID")


def test_publicly_rehashed_route_advice_is_not_a_runner_or_issuer(evidence):
    # Existing Mono advisory verification accepts this structural digest. The
    # diagnostic must not treat that fact as producer or claim authentication.
    forged = {**evidence["assessment"], "state": "ROUTE_PROPOSED", "reason": None,
        "route": {"adapter_id": "invented", "route_id": "invented"}}
    forged.pop("assessment_digest")
    forged["assessment_digest"] = hashlib.sha256(json.dumps(forged, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert verify_portal_route_assessment(evidence["ledger"], evidence["request"], forged) == forged
    held(run(evidence, assessment=forged), "ADVISORY_NOT_QUALIFIED_HOST_OBSERVATION")
    unchanged(evidence)


def test_fabricated_operational_success_is_not_canonical_completion(evidence):
    fabricated = json.dumps({"succeeded": True, "classification": "COMPLETED", "work_fingerprint": evidence["claim"]["work_fingerprint"]})
    with closed_db(evidence["runner_db"]) as db:
        db.execute("UPDATE execution_attempts SET result_json=?,result_sha256=?", (fabricated, hashlib.sha256(fabricated.encode()).hexdigest()))
    report = run(evidence)
    held(report, "OPERATIONAL_RESULT_NOT_CANONICAL_EVIDENCE")
    assert not evidence["ledger"].read(evidence["intake"]["task_id"]).closed


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "1001"])
def test_unqualified_clock_does_not_validate_a_lease(evidence, bad):
    held(run(evidence, now=bad), "CLAIM_EVIDENCE_INVALID")


def test_wal_is_not_ignored_to_make_the_old_main_file_pass(evidence):
    wal = Path(str(evidence["runner_db"]) + "-wal")
    wal.write_bytes(b"ADVERSARIAL NONEMPTY WAL: NOT A CHECKPOINTED DB")
    held(run(evidence), "RUNNER_WAL_UNQUALIFIED")
    assert wal.read_bytes().startswith(b"ADVERSARIAL")


def test_copied_database_cannot_be_authenticated_by_repinning_the_declared_root(evidence, tmp_path):
    copied = tmp_path / "copied.sqlite3"
    copied.write_bytes(evidence["runner_db"].read_bytes())
    held(run(evidence, runner_db=copied, expected_runner_db=copied), "CLAIM_DOMAIN_NOT_AUTHENTICATED")
    assert copied.read_bytes() == evidence["runner_db"].read_bytes()


@pytest.mark.parametrize("statement,reason", [
    ("DELETE FROM leases", "LEASE_INVALID"),
    ("UPDATE recursive_work_state SET status='SUPERSEDED'", "CLAIM_EVIDENCE_INVALID"),
    ("UPDATE recursive_work_state SET generation=3", "CLAIM_EVIDENCE_INVALID"),
    ("UPDATE lineage_budgets SET generation=3", "CLAIM_EVIDENCE_INVALID"),
    ("UPDATE execution_attempts SET attempt_sha256='forged'", "CLAIM_EVIDENCE_INVALID"),
])
def test_corrupt_disposable_claim_rows_do_not_become_valid_by_metadata(evidence, statement, reason):
    with closed_db(evidence["runner_db"]) as db:
        db.execute(statement)
    before = sha(evidence["runner_db"])
    held(run(evidence), reason)
    assert sha(evidence["runner_db"]) == before


def test_same_nominal_task_in_an_independent_root_is_not_authenticated_identity(evidence, tmp_path):
    other = TaskExecutionLedger(tmp_path / "other-mono/tasks.sqlite3")
    same = open_message(other, "diagnostic-conversation", "original-request", LITERAL)
    assert same == evidence["intake"]
    held(run(evidence, ledger=other), "CLAIM_DOMAIN_NOT_AUTHENTICATED")
    assert len(other.events(same["task_id"])) == 1


@pytest.mark.parametrize("field,value", [("text", "changed source"), ("conversation_id", "other-conversation"), ("message_id", "other-request"), ("source_digest", "0" * 64), ("opened_head", "0" * 64)])
def test_original_task_locator_and_literal_cannot_be_substituted(evidence, field, value):
    intake = {**evidence["intake"], field: value}
    held(run(evidence, intake=intake), "TASK_SOURCE_INVALID")
    with pytest.raises(ConversationError):
        open_message(evidence["ledger"], evidence["intake"]["conversation_id"], evidence["intake"]["message_id"], "changed source")
    unchanged(evidence)


def test_independent_task_advances_global_advice_but_not_original_task_head(evidence):
    open_message(evidence["ledger"], "diagnostic-conversation", "task-B", "Independent task B")
    report = run(evidence)
    held(report, "ADVISORY_STALE_OR_INVALID")
    assert report["task_head"] == evidence["intake"]["opened_head"]
    assert report["global_head"] != report["task_head"]


def test_advanced_task_head_is_separate_from_immutable_opener(evidence):
    i = evidence["intake"]
    evidence["ledger"].checkpoint(i["task_id"], "fixture-advance", completed_evidence=("TEST ONLY",),
        blockers=("HOLD",), protected_effects_still_gated=("all",), next_frontier="HOLD",
        lifecycle_evidence_digest=i["source_digest"], expected_task_head=i["opened_head"])
    report = run(evidence)
    held(report, "TASK_ADVANCED_NO_CORRELATED_OBSERVATION")
    assert report["task_head"] != i["opened_head"]


def test_diagnostic_reads_only_and_does_not_reenter_admission(evidence, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic reentered admission/store initialization")
    monkeypatch.setattr(SqliteDispatchAdmissionStore, "__init__", forbidden)
    original_connect = sqlite3.connect
    def checked_connect(path, *args, **kwargs):
        if "runner.sqlite3" in str(path):
            assert "mode=ro" in str(path) and kwargs.get("uri") is True
        return original_connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", checked_connect)
    before = sha(evidence["runner_db"])
    held(run(evidence))
    held(run(evidence))
    assert sha(evidence["runner_db"]) == before
    assert len(evidence["transport"].reads) == 1
    assert not Path(str(evidence["runner_db"]) + "-wal").exists()
    assert not Path(str(evidence["runner_db"]) + "-shm").exists()


def test_original_held_receipt_reopens_in_a_real_separate_process(evidence, tmp_path):
    first = run(evidence)
    data = {k: v for k, v in evidence.items() if k not in {"ledger", "transport"}}
    data["runner_db"] = str(data["runner_db"])
    data["expected_runner_db"] = str(data["expected_runner_db"])
    data["task_db"] = str(evidence["ledger"].path)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(data), encoding="utf-8")
    code = '''
import importlib.util,json,sys,os
from pathlib import Path
from vera_core.task_execution import TaskExecutionLedger
p=json.loads(Path(sys.argv[2]).read_text()); p['ledger']=TaskExecutionLedger(p.pop('task_db'))
s=importlib.util.spec_from_file_location('diagnostic',sys.argv[1]); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
r=m.diagnose(**p); print(json.dumps({'pid':os.getpid(),'receipt':r},sort_keys=True))
'''
    before = {str(p): sha(p) for p in (evidence["runner_db"], evidence["ledger"].path)}
    cold = subprocess.run([sys.executable, "-B", "-c", code, str(DIAGNOSTIC), str(inputs)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=20)
    assert cold.returncode == 0, cold.stderr
    result = json.loads(cold.stdout)
    assert result["pid"] != os.getpid()
    assert result["receipt"] == first
    held(result["receipt"], "MONO_WORKUNIT_PRODUCER_ABI_MISSING")
    assert before == {str(p): sha(Path(p)) for p in before}
    unchanged(evidence)
    # Interruption after observation, before delivery, cannot create a retry ID
    # or a new claim. The diagnostic has no dispatch/recovery mutation path.
    interrupted = subprocess.run([sys.executable, "-B", "-c", code + "\nos._exit(17)\n", str(DIAGNOSTIC), str(inputs)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=20)
    assert interrupted.returncode == 17
    assert run(evidence) == first
    assert before == {str(p): sha(Path(p)) for p in before}
    (tmp_path / "cold-receipt.json").write_text(json.dumps({"parent_pid": os.getpid(), **result}, indent=2), encoding="utf-8")
