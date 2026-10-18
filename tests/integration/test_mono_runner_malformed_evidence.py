"""VN1-reported candidate independently reproduced by implementation child D.

Not VN1 certification. Contract expectation: malformed caller evidence still
returns a structured diagnostic HOLD/NO_EFFECT, never an unhandled exception.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

pytest.importorskip("vera_core", reason="requires selected pinned Mono source")
from vera_core import QualifiedVeraRuntime, VeraStateDirectory
from vera_core.local_conversation import open_message
from vera_core.portal_task_handoff import export_portal_route_request
from portal.vera_task_handoff import assess_vera_task_route

HERE = Path(__file__).resolve().parent
HELPER = HERE.parent / "diagnostics/mono_runner_binding_probe.py"
HELPER_BYTES = HELPER.read_bytes()
HELPER_SHA = hashlib.sha256(HELPER_BYTES).hexdigest()
spec = importlib.util.spec_from_file_location("malformed_current_claim_probe", HELPER)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

CASES = [
    ("baseline", None, None),
    ("intake-none", "intake", None),
    ("intake-list", "intake", []),
    ("intake-text", "intake", "forged"),
    ("intake-empty-map", "intake", {}),
    ("request-none", "request", None),
    ("request-list", "request", []),
    ("request-text", "request", "forged"),
    ("request-empty-map", "request", {}),
    ("assessment-none", "assessment", None),
    ("assessment-list", "assessment", []),
    ("assessment-text", "assessment", "forged"),
    ("assessment-empty-map", "assessment", {}),
    ("runner-db-none", "runner_db", None),
    ("runner-db-list", "runner_db", []),
    ("expected-db-none", "expected_runner_db", None),
    ("expected-db-list", "expected_runner_db", []),
]


@pytest.mark.parametrize("case,field,bad", CASES, ids=[c[0] for c in CASES])
def test_malformed_evidence_returns_structured_hold(tmp_path, case, field, bad):
    rt = QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(
        tmp_path / "mono", project_id="DISPOSABLE_D_MALFORMED", identity_id="UNTRUSTED_D_FIXTURE"))
    intake = open_message(rt.tasks, "untrusted-malformed-conversation", "original-request", "Original literal Î»")
    request = export_portal_route_request(rt.tasks, intake["task_id"], node_id="untrusted-fixture",
        target_kind="repository", target_id="thebrazenbeard/project-runner", required_capabilities=("source_read",))
    assessment = assess_vera_task_route(request, ())
    params = dict(ledger=rt.tasks, intake=intake, request=request, assessment=assessment,
        runner_db=tmp_path / "missing.sqlite", expected_runner_db=tmp_path / "missing.sqlite", claim=None, now=1001.0)
    if field is not None:
        params[field] = bad
    if field in ("runner_db", "expected_runner_db"):
        # Non-null untrusted claim exercises path validation; no store exists,
        # constructor runs, claim is admitted, or live Runner root is accessed.
        params["claim"] = {}
    before = tuple(rt.tasks.events(intake["task_id"]))
    observed = {"case": case, "helper_sha256": HELPER_SHA, "pid": os.getpid(),
        "task_id": intake["task_id"], "source_digest": intake["source_digest"]}
    try:
        result = probe.diagnose(**params)
    except Exception as exc:
        observed.update(exception_type=type(exc).__name__, exception=str(exc))
        raise
    else:
        observed["receipt"] = result
        assert result["state"] == "HOLD" and result["effect"] == "NO_EFFECT"
        assert result["dispatch_authorized"] is result["producer_authenticated"] is False
        assert result["dispatches"] == result["model_calls"] == result["runner_writes"] == 0
    finally:
        assert tuple(rt.tasks.events(intake["task_id"])) == before
        assert not rt.tasks.read(intake["task_id"]).closed
        assert rt.pc_execution_transport is None and not rt.provider_execution_transports
        assert HELPER.read_bytes() == HELPER_BYTES
        (tmp_path / ("malformed-observed-" + case + ".json")).write_text(
            json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
