"""Actual full-runtime composition. All claims are disposable, offline fixtures.

This is not an issuer, admission adapter, authenticated receipt or effect route.
The existing producer lacks Mono correlation; every composed claim stays HOLD.
"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict, replace
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

PORTAL = Path(__file__).resolve().parents[2]
pytest.importorskip("vera_core", reason="requires selected pinned Mono source")
from vera_core import QualifiedVeraRuntime, VeraStateDirectory
from vera_core.local_conversation import (
    ConversationError, open_message, recover_message, reserve_source_trial,
)
from vera_core.portal_task_handoff import export_portal_route_request
from pre_active import Store
from portal import PortalRouteAdvertisement
from portal.host_probe import PortalCommandHostProbe
from portal.vera_task_handoff import assess_vera_task_route
from runner.cli import main
from runner.durable_dispatch import SqliteDispatchAdmissionStore
from runner.portfolio_operator_bridge import claim_bound_plan_subject
from runner.recursive_state import _work_from_payload
from runner.work_units import WorkUnitStatus, work_unit_fingerprint

CID = "untrusted-source-c"
LITERAL = "Read-only source λ original bytes"
OFFLINE_HEAD = "c" * 40
PROBE = PORTAL / "tests/diagnostics/mono_runner_binding_probe.py"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime(root):
    return QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(
        root, project_id="DISPOSABLE_SOURCE_FIXTURE", identity_id="UNTRUSTED_FIXTURE_ID"))


@contextmanager
def readonly(path):
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    db = sqlite3.connect(Path(path).as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    try:
        yield db
        assert db.total_changes == 0
    finally:
        db.rollback()
        db.close()


def diagnose(data, rt, intake, *, request=None, assessment=None):
    spec = importlib.util.spec_from_file_location("held_probe_c", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = request or export_portal_route_request(rt.tasks, intake["task_id"],
        node_id="source-fixture", target_kind="repository",
        target_id=data["claim"]["repository"], required_capabilities=("source_read",))
    assessment = assessment or assess_vera_task_route(request, ())
    return module.diagnose(ledger=rt.tasks, intake=intake, request=request,
        assessment=assessment, runner_db=data["runner_db"],
        expected_runner_db=data["runner_db"], claim=data["claim"], now=1001.0)


def held(receipt):
    assert receipt["state"] == "HOLD" and receipt["effect"] == "NO_EFFECT"
    assert receipt["dispatch_authorized"] is receipt["producer_authenticated"] is False
    assert receipt["dispatches"] == receipt["model_calls"] == receipt["runner_writes"] == 0
    assert "MONO_WORKUNIT_PRODUCER_ABI_MISSING" in receipt["reasons"]
    assert "CLAIM_DOMAIN_NOT_AUTHENTICATED" in receipt["reasons"]


def protected_counts(root):
    result = {}
    for folder in ("memory", "effects", "trust", "provider", "pc", "source"):
        for path in sorted((root / folder).glob("*.sqlite")):
            with readonly(path) as db:
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                result[str(path.relative_to(root))] = {
                    name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                    for name in tables}
    return result


@pytest.fixture
def composition(tmp_path):
    root = tmp_path / "mono"
    rt = runtime(root)
    assert type(rt) is QualifiedVeraRuntime
    assert rt.pc_execution_transport is None
    assert not rt.provider_execution_transports and not rt.source_mutation_transports
    a = open_message(rt.tasks, CID, "request-A", LITERAL)
    initial_counts = protected_counts(root)
    class OfflineRead:
        def __init__(self): self.reads = []
        def read_ref(self, repository, ref):
            assert (repository, ref) == ("thebrazenbeard/project-runner", "main")
            assert len(rt.tasks.events(a["task_id"])) == 1
            self.reads.append((repository, ref))
            return OFFLINE_HEAD  # Explicit stub, never target-source qualification.
        def read_file(self, *args): return None
        def create_branch(self, *args): raise AssertionError("effect forbidden")
        def put_file(self, *args, **kwargs): raise AssertionError("effect forbidden")
    out = StringIO()
    with redirect_stdout(out):
        assert main(["portfolio-wave-plan", "--wave", str(PORTAL / "portfolio/advancement_wave.public.json"),
            "--max-parallel", "50", "--max-per-identity", "50", "--max-per-family", "50"]) == 0
    plan = tmp_path / "plan.json"
    plan.write_text(out.getvalue(), encoding="utf-8")
    offline = OfflineRead()
    db = tmp_path / "runner.sqlite3"
    claim = claim_bound_plan_subject(plan_path=plan,
        wave_path=PORTAL / "portfolio/advancement_wave.public.json",
        corpus_path=PORTAL / "portfolio/corpus.public.json",
        projects_path=PORTAL / "registry/projects.yaml", subject_id="project-runner",
        state_db=db, holder="UNAPPROVED_OFFLINE_C_FIXTURE", lease_ttl=60,
        allowed_repositories=("thebrazenbeard/project-runner",),
        transport=offline, clock=lambda: 1000.0)
    assert len(offline.reads) == 1 and claim.fencing_token == 1
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()
    data = {"runner_db": str(db), "claim": asdict(claim), "mono_root": str(root), "intake": a}
    yield data, rt, offline
    assert protected_counts(root) == initial_counts
    with readonly(db) as conn:
        assert tuple(conn.execute("SELECT count(*),count(result_json) FROM execution_attempts").fetchone()) == (1, 0)
    assert len(offline.reads) == 1


def test_full_runtime_precedes_genuine_existing_producer_and_actual_noeffect_probe(composition):
    data, rt, offline = composition
    raw = {"schema": "PORTAL_HOST_PROBE_RESULT_V1", "adapter_id": "c-offline-source",
        "evidence_id": "UNAPPROVED_DISPOSABLE_SOURCE_OBSERVATION", "occupancy": [],
        "routes": [{"route_id": "source-read", "node_id": "source-fixture",
            "target_kind": "repository", "target_id": data["claim"]["repository"],
            "capabilities": ["source_read"], "effect_capabilities": [],
            "available": True, "attached": True, "current": True, "preference": 0}]}
    observation = PortalCommandHostProbe(adapter_id="c-offline-source", command=(
        sys.executable, "-B", "-c", "import json;print(json.dumps(" + repr(raw) + "))")).observe()
    assert all(not r.authorized_effects and not r.effect_capabilities for r in observation.routes)
    request = export_portal_route_request(rt.tasks, data["intake"]["task_id"],
        node_id="source-fixture", target_kind="repository",
        target_id=data["claim"]["repository"], required_capabilities=("source_read",))
    advice = assess_vera_task_route(request, observation.routes)
    assert advice["state"] == "HELD" and advice["route"] is None
    receipt = diagnose(data, rt, data["intake"], request=request, assessment=advice)
    held(receipt)
    assert receipt["lease_checks"] == "VALID_AT_FIXTURE_CLOCK_ONLY"
    assert not rt.tasks.read(data["intake"]["task_id"]).closed


def test_one_existing_claim_can_be_presented_with_two_tasks_but_authenticates_neither(composition):
    data, rt, offline = composition
    a = data["intake"]
    b = open_message(rt.tasks, CID, "request-B", "Independent source B")
    replay = open_message(rt.tasks, CID, "request-A", LITERAL)
    assert replay == a
    assert len(rt.tasks.events(a["task_id"])) == len(rt.tasks.events(b["task_id"])) == 1
    assert rt.tasks.events(a["task_id"])[-1].event_digest == a["opened_head"]
    assert rt.tasks.read(a["task_id"]).journal_head_digest != a["opened_head"]
    ra, rb = diagnose(data, rt, a), diagnose(data, rt, b)
    held(ra); held(rb)
    assert ra["work_fingerprint"] == rb["work_fingerprint"]
    assert ra["task_id"] != rb["task_id"] and ra["source_digest"] != rb["source_digest"]
    with readonly(data["runner_db"]) as conn:
        work = conn.execute("SELECT work_json FROM recursive_work_state").fetchone()[0]
        assert a["task_id"] not in work and b["task_id"] not in work


def test_unapproved_payload_proposal_is_immutable_but_not_existing_producer_admission(composition):
    data, rt, offline = composition
    a = data["intake"]
    b = open_message(rt.tasks, CID, "request-B", "Independent source B")
    before = sha(data["runner_db"])
    with readonly(data["runner_db"]) as conn:
        row = conn.execute("SELECT work_json FROM recursive_work_state").fetchone()
        work = _work_from_payload(json.loads(row[0]), status=WorkUnitStatus.CLAIMED)
    def proposed(intake):
        # Source proposal only: no owner-approved issuer/domain, no admission.
        return replace(work, payload={**work.payload, "UNAPPROVED_MONO_ASSOCIATION_PROPOSAL": {
            "task_id": intake["task_id"], "packet_digest": intake["packet_digest"],
            "source_digest": intake["source_digest"], "opened_head": intake["opened_head"],
            "expected_task_head": rt.tasks.events(intake["task_id"])[-1].event_digest,
            "head_scope": "TASK", "origin_authenticated": False,
            "effect": "NO_EFFECT", "dispatch_authorized": False}})
    assert work_unit_fingerprint(proposed(a)) != work_unit_fingerprint(proposed(b))
    assert work_unit_fingerprint(proposed(a)) != data["claim"]["work_fingerprint"]
    assert sha(data["runner_db"]) == before
    held(diagnose(data, rt, a))


def test_task_scoped_source_reservation_survives_unrelated_global_advance(composition, tmp_path):
    data, rt, offline = composition
    a = data["intake"]
    open_message(rt.tasks, CID, "request-B", "Independent source B")
    request = export_portal_route_request(rt.tasks, a["task_id"], node_id="veraos",
        target_kind="MODEL", target_id="offline-never-run", required_capabilities=("text",))
    fixture_advice = assess_vera_task_route(request, (PortalRouteAdvertisement(
        adapter_id="unapproved-test-fixture", route_id="offline-never-run", node_id="veraos",
        target_kind="MODEL", target_id="offline-never-run", capabilities=("text",),
        effect_capabilities=("NO_PROTECTED_EFFECT",), authorized_effects=("NO_PROTECTED_EFFECT",),
        available=True, attached=True, current=True),))
    reservation = reserve_source_trial(rt.tasks, a, fixture_advice,
        target_id="offline-never-run", route_id="offline-never-run")
    assert reservation["seed"]["model_admitted"] is reservation["seed"]["dispatch_authorized"] is False
    source_path = tmp_path / "unrun-source.sqlite3"
    source = Store(source_path)
    try:
        assert recover_message(rt.tasks, source, a)["status"] == "OUTCOME_UNKNOWN_NO_REDISPATCH"
    finally:
        source.close()
    assert len(rt.tasks.events(a["task_id"])) == 2
    assert a["opened_head"] != rt.tasks.events(a["task_id"])[-1].event_digest
    held(diagnose(data, rt, a))
    with pytest.raises(ConversationError, match="already exists|advanced"):
        reserve_source_trial(rt.tasks, a, fixture_advice, target_id="offline-never-run", route_id="offline-never-run")
    inputs = tmp_path / "source-cold-inputs.json"
    inputs.write_text(json.dumps({**data, "source_db": str(source_path)}), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--worker", str(inputs), "cold"],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
    assert p.returncode == 0, p.stderr
    recovered = json.loads(p.stdout)
    assert recovered["pid"] != os.getpid() and recovered["event_count"] == 2
    assert recovered["source_recovery"]["status"] == "OUTCOME_UNKNOWN_NO_REDISPATCH"
    held(recovered["receipt"])
    (tmp_path / "interrupted-source-cold-receipt.json").write_text(json.dumps(recovered, indent=2), encoding="utf-8")


def test_literal_substitution_rejected_after_other_task_without_new_claim(composition):
    data, rt, offline = composition
    open_message(rt.tasks, CID, "request-B", "Independent source B")
    before = sha(rt.tasks.path)
    for text in (LITERAL + " ", "changed bytes", " " + LITERAL):
        with pytest.raises(ConversationError):
            open_message(rt.tasks, CID, "request-A", text)
    assert sha(rt.tasks.path) == before
    assert open_message(rt.tasks, CID, "request-A", LITERAL) == data["intake"]


def cold_worker(path, interrupted=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rt = runtime(Path(data["mono_root"]))
    intake = open_message(rt.tasks, CID, "request-A", LITERAL)
    assert intake == data["intake"]
    def forbidden(*args, **kwargs): raise AssertionError("cold diagnostic attempted mutable admission constructor")
    SqliteDispatchAdmissionStore.__init__ = forbidden
    receipt = diagnose(data, rt, intake)
    held(receipt)
    result = {"pid": os.getpid(), "receipt": receipt,
        "intake": intake, "event_count": len(rt.tasks.events(intake["task_id"])),
        "protected_counts": protected_counts(Path(data["mono_root"]))}
    if data.get("source_db"):
        source = Store(data["source_db"])
        try:
            result["source_recovery"] = recover_message(rt.tasks, source, intake)
        finally:
            source.close()
    if interrupted:
        # Test-only process witness; no task/claim result is delivered.
        Path(path).with_name("interrupted-child-pid.json").write_text(json.dumps({
            "pid": os.getpid(), "outcome_delivered": False}), encoding="utf-8")
        os._exit(17)  # Deliberate interruption after observation, before delivery.
    print(json.dumps(result, sort_keys=True))


def test_full_runtime_cold_interrupted_receipt_never_reclaims_or_dispatches(composition, tmp_path):
    data, rt, offline = composition
    open_message(rt.tasks, CID, "request-B", "Independent source B")
    inputs = tmp_path / "cold-inputs.json"
    inputs.write_text(json.dumps(data), encoding="utf-8")
    first = diagnose(data, rt, data["intake"])
    before = {str(p): sha(p) for p in (Path(data["runner_db"]), rt.tasks.path)}
    counts = protected_counts(Path(data["mono_root"]))
    children = []
    for mode in ("cold", "interrupt", "cold"):
        p = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--worker", str(inputs), mode],
            capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
        if mode == "interrupt":
            assert p.returncode == 17, p.stderr
        else:
            assert p.returncode == 0, p.stderr
            result = json.loads(p.stdout)
            assert result["pid"] != os.getpid()
            assert result["receipt"] == first
            assert result["event_count"] == 1 and result["protected_counts"] == counts
            children.append(result)
    assert children[0]["pid"] != children[1]["pid"]
    interrupted = json.loads(inputs.with_name("interrupted-child-pid.json").read_text(encoding="utf-8"))
    assert interrupted["pid"] not in {os.getpid(), *(child["pid"] for child in children)}
    assert interrupted["outcome_delivered"] is False
    assert before == {path: sha(path) for path in before}
    (tmp_path / "full-runtime-cold-receipt.json").write_text(json.dumps({
        "parent_pid": os.getpid(), "cold_children": children,
        "interrupted_child": interrupted,
        "interrupted_child_exit": 17, "claim_admissions": 1,
        "provider_model_dispatches": 0, "runner_readonly_after_claim": True}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    assert sys.argv[1] == "--worker"
    cold_worker(sys.argv[2], interrupted=sys.argv[3] == "interrupt")
