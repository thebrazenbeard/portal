"""Diagnostic test artifact, NOT a production admission or receipt authority.

Consumes an existing Mono ledger and a CLOSED disposable Runner fixture. Runner
is opened read-only, without constructing its migrating stores. A current-looking
fixture lease cannot supply the missing approved immutable Mono producer ABI.
Every result is HOLD/NO_EFFECT. No positive execution path exists here.
"""
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from vera_core.portal_task_handoff import verify_portal_route_assessment
from vera_core.task_execution import TaskExecutionError
from runner.durable_dispatch import _attempt_digest
from runner.leases import Lease
from runner.portfolio_operator_bridge import BoundPlanClaim
from runner.recursive_state import _immutable_digest, _validate_active_lease_state, _work_from_payload
from runner.work_units import WorkUnitStatus, work_unit_fingerprint


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _claim_evidence(db, claim, now, reasons, report):
    if type(claim) is not dict or set(claim) != set(BoundPlanClaim.__dataclass_fields__):
        reasons.add("CLAIM_EVIDENCE_INVALID")
        return
    if (type(claim["fencing_token"]) is not int or claim["fencing_token"] < 1
            or type(claim["work_generation"]) is not int or type(claim["budget_generation"]) is not int
            or type(claim["lease_expires_at"]) not in (int, float)
            or not math.isfinite(claim["lease_expires_at"])):
        reasons.add("CLAIM_EVIDENCE_INVALID")
        return
    params = (claim["lineage_id"], claim["work_fingerprint"])
    work = db.execute("SELECT * FROM recursive_work_state WHERE lineage_id=? AND work_fingerprint=?", params).fetchone()
    attempt = db.execute("SELECT * FROM execution_attempts WHERE lineage_id=? AND work_fingerprint=? AND fencing_token=?", (*params, claim["fencing_token"])).fetchone()
    lease = db.execute("SELECT * FROM leases WHERE work_fingerprint=?", (claim["work_fingerprint"],)).fetchone()
    # Check the existing native guard even when a forged fence names no attempt.
    try:
        _validate_active_lease_state(db, work_fingerprint_value=claim["work_fingerprint"],
            lease=Lease(claim["work_fingerprint"], claim["holder"], claim["fencing_token"], claim["lease_expires_at"]), now=now)
        report["lease_checks"] = "VALID_AT_FIXTURE_CLOCK_ONLY"
    except ValueError:
        reasons.add("LEASE_INVALID")
    if work is None or lease is None or attempt is None:
        reasons.add("CLAIM_EVIDENCE_INVALID")
        return
    budget = db.execute("SELECT * FROM lineage_budgets WHERE lineage_id=? AND scope_id=?", (claim["lineage_id"], work["budget_scope_id"])).fetchone()
    payload = json.loads(work["work_json"])
    immutable = _immutable_digest(work_json=work["work_json"], lineage_id=work["lineage_id"],
        budget_scope_id=work["budget_scope_id"], parent_fingerprint=work["parent_fingerprint"],
        ancestry_json=work["ancestry_json"], effective_capabilities_json=work["effective_capabilities_json"])
    semantic = work_unit_fingerprint(_work_from_payload(payload, status=WorkUnitStatus(work["status"])))
    if immutable != work["immutable_sha256"] or semantic != claim["work_fingerprint"]:
        reasons.add("WORK_INTEGRITY_INVALID")
    report["work_immutable_digest"] = immutable
    report["work_fingerprint"] = semantic
    producer = payload["payload"]
    expected_fields = {"schema", "subject_id", "operator_project_id", "operator_registry_sha256", "plan_sha256", "wave_sha256", "selected", "repository", "ref", "exact_head", "execution_authority", "protected_effects_authorized"}
    if (payload["operation"] != "PORTFOLIO_BOUND_CLAIM" or set(producer) != expected_fields
            or producer["schema"] != "PROJECT_RUNNER_BOUND_PLAN_CLAIM_V1"
            or producer["execution_authority"] is not False or producer["protected_effects_authorized"] is not False):
        reasons.add("CLAIM_PRODUCER_UNQUALIFIED")
    if (any(producer.get(k) != claim[k] for k in ("subject_id", "repository", "ref", "exact_head", "plan_sha256", "wave_sha256"))
            or payload["inputs"] != [{"repository": claim["repository"], "ref": claim["ref"], "commit": claim["exact_head"], "path": None, "digest": None}]
            or lease["expires_at"] != claim["lease_expires_at"] or attempt["holder"] != claim["holder"]
            or budget is None or budget["generation"] != attempt["budget_generation"] or budget["generation"] != claim["budget_generation"]
            or work["generation"] != attempt["work_generation"] or work["generation"] != claim["work_generation"]
            or work["status"] != "CLAIMED"):
        reasons.add("CLAIM_EVIDENCE_INVALID")
    actual_attempt = _attempt_digest(lineage_id=attempt["lineage_id"], work_fingerprint_value=attempt["work_fingerprint"],
        fencing_token=attempt["fencing_token"], holder=attempt["holder"], admitted_at=attempt["admitted_at"],
        budget_generation=attempt["budget_generation"], work_generation=attempt["work_generation"])
    if actual_attempt != attempt["attempt_sha256"]:
        reasons.add("CLAIM_EVIDENCE_INVALID")
    # Operational results cannot supply canonical completion or authenticity.
    if attempt["result_json"] is None or attempt["result_sha256"] is None:
        reasons.add("OUTCOME_UNKNOWN_NO_REDISPATCH")
    else:
        reasons.add("OPERATIONAL_RESULT_NOT_CANONICAL_EVIDENCE")


def diagnose(*, ledger, intake, request, assessment, runner_db, expected_runner_db, claim, now):
    """Explain negative source evidence, never repair/qualify/dispatch a claim.

    expected_runner_db is a caller-declared fixture domain, NOT authenticated
    host identity. Matching or copied bytes, valid public hashes and any added
    Mono metadata can never turn this diagnostic's output into an acceptance.
    """
    reasons = {"MONO_WORKUNIT_PRODUCER_ABI_MISSING", "CLAIM_DOMAIN_NOT_AUTHENTICATED"}
    if type(intake) is not dict:
        reasons.add("TASK_SOURCE_INVALID")
        intake = {}
    if type(request) is not dict:
        reasons.add("ADVISORY_STALE_OR_INVALID")
        request = {}
    if type(assessment) is not dict:
        reasons.add("ADVISORY_STALE_OR_INVALID")
        assessment = {}
    report = {"schema": "DIAGNOSTIC_ONLY_MONO_RUNNER_BINDING_V1", "state": "HOLD", "effect": "NO_EFFECT",
        "dispatch_authorized": False, "producer_authenticated": False, "model_calls": 0,
        "dispatches": 0, "runner_writes": 0, "lease_checks": "NOT_VALIDATED",
        "task_id": intake.get("task_id"), "source_digest": intake.get("source_digest"),
        "opened_head": intake.get("opened_head"), "request_digest": request.get("request_digest")}
    try:
        state = ledger.read(intake["task_id"])
        events = ledger.events(intake["task_id"])
        report["global_head"] = state.journal_head_digest
        report["task_head"] = events[-1].event_digest
        purpose = json.loads(state.packet.purpose.removeprefix("VERAOS_LOCAL_MESSAGE_V1:"))
        if (state.closed or state.active_dependencies or state.unresolved_correction_ids
                or purpose != {"conversation_id": intake["conversation_id"], "message_id": intake["message_id"]}
                or state.packet.subject != intake["text"] or state.packet.packet_digest != intake["packet_digest"]
                or state.opened_lifecycle_evidence_digest != intake["source_digest"]
                or events[0].event_type != "TASK_OPENED" or events[0].event_digest != intake["opened_head"]
                or request["task_id"] != intake["task_id"]):
            reasons.add("TASK_SOURCE_INVALID")
        if events[-1].event_digest != intake["opened_head"]:
            reasons.add("TASK_ADVANCED_NO_CORRELATED_OBSERVATION")
        try:
            verify_portal_route_assessment(ledger, request, assessment)
        except (TaskExecutionError, ValueError, TypeError, KeyError):
            reasons.add("ADVISORY_STALE_OR_INVALID")
        if assessment.get("state") != "HELD" or assessment.get("route") is not None:
            reasons.add("ADVISORY_NOT_QUALIFIED_HOST_OBSERVATION")
    except (TaskExecutionError, ValueError, TypeError, KeyError, IndexError):
        reasons.add("TASK_SOURCE_INVALID")
    if type(now) not in (int, float) or not math.isfinite(now):
        reasons.add("CLAIM_EVIDENCE_INVALID")
    elif claim is None:
        reasons.add("CLAIM_MISSING")
    else:
        try:
            path = Path(runner_db).resolve()
            expected_path = Path(expected_runner_db).resolve()
        except (TypeError, ValueError, OSError):
            path = expected_path = None
        if path is None:
            reasons.add("RUNNER_DB_UNAVAILABLE")
        elif path != expected_path:
            reasons.add("CLAIM_DOMAIN_MISMATCH")
        # Closed fixtures only. immutable avoids SQLite creating WAL/SHM even
        # for a mode=ro connection to a WAL-mode DB. Never use this as a live
        # lease-currentness protocol: the report is unconditionally HOLD.
        elif Path(str(path) + "-wal").exists() or Path(str(path) + "-shm").exists():
            reasons.add("RUNNER_WAL_UNQUALIFIED")
        else:
            db = None
            before = None
            try:
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0.1)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                _claim_evidence(db, claim, now, reasons, report)
                assert db.total_changes == 0
            except (sqlite3.Error, OSError):
                reasons.add("RUNNER_DB_UNAVAILABLE")
            except (ValueError, TypeError, KeyError, IndexError):
                reasons.add("CLAIM_EVIDENCE_INVALID")
            finally:
                if db is not None:
                    db.rollback()
                    db.close()
            if before is not None:
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
                        reasons.add("RUNNER_SNAPSHOT_CHANGED")
                except OSError:
                    reasons.add("RUNNER_SNAPSHOT_CHANGED")
    report["reasons"] = sorted(reasons)
    report["receipt_sha256"] = hashlib.sha256(_canonical(report).encode("utf-8")).hexdigest()
    return report
