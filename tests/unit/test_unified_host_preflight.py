"""Source-only falsification of cross-host readiness claims."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from portal.unified_host_preflight import REQUIRED_PROBES, SCHEMA, evaluate_preflight, main


NOW = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)


def evidence() -> dict:
    observations = []
    for gate_id, kind in REQUIRED_PROBES.items():
        observations.append({
            "id": gate_id, "kind": kind,
            "status": "RUNNING" if kind == "WINDOWS_SERVICE" else 200,
            "credential_applied": kind == "AUTHENTICATED_HTTP",
        })
    return {
        "schema": SCHEMA,
        "observed_at_utc": "2026-11-05T23:00:00Z",
        "observations": observations,
        "model_inventory": [{
            "host": "control", "model_tag": "vera-local:latest",
            "digest_kind": "OLLAMA_TAG_DIGEST", "digest_sha256": "a" * 64,
        }],
        "model_selection": {
            "host": "control", "model_tag": "vera-local:latest",
            "digest_kind": "OLLAMA_TAG_DIGEST", "digest_sha256": "a" * 64,
            "incremental_paid_compute": False,
        },
    }


def probe(payload: dict, gate_id: str) -> dict:
    return next(x for x in payload["observations"] if x["id"] == gate_id)


def test_complete_source_only_report_is_not_deployment_or_effect_authority():
    result = evaluate_preflight(evidence(), now=NOW)
    assert result["status"] == "OBSERVATIONAL_PREFLIGHT_PASS"
    assert result["deployment_authorized"] is False
    assert result["effect_authority_granted"] is False
    assert result["next_gate"] == "LIVE_INSTALL_ROUTE_RUNTIME_CONSUMPTION_AND_EFFECT_QUALIFICATION"


def test_actual_lappy_stopped_services_hold_even_with_live_model_inventory():
    report = evidence()
    probe(report, "control.vera_mono_pilot")["status"] = "STOPPED"
    probe(report, "control.veramesh_tunnel")["status"] = "STOPPED"
    result = evaluate_preflight(report, now=NOW)
    assert result["status"] == "HOLD"
    assert "control.vera_mono_pilot is STOPPED" in result["reason_codes"]
    assert "control.veramesh_tunnel is STOPPED" in result["reason_codes"]


def test_reconstructed_host_report_is_hold_and_not_attested():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "unified_host_reported_hold.json"
    report = json.loads(fixture.read_text(encoding="utf-8"))
    result = evaluate_preflight(
        report, now=datetime(2026, 11, 5, 23, 23, 30, tzinfo=timezone.utc),
    )
    assert result["status"] == "HOLD"
    assert "worker_b.rezon_readyz lacks an authenticated probe" in result["reason_codes"]
    assert "selected local model digest not evidenced" in result["reason_codes"]
    assert result["evidence_class"] == "OPERATOR_REPORTED_OBSERVATION_NOT_INDEPENDENT_ATTESTATION"


def test_rezon_unauthenticated_401_is_not_ready_or_down():
    report = evidence()
    target = probe(report, "worker_b.rezon_readyz")
    target["status"] = 401
    target["credential_applied"] = False
    result = evaluate_preflight(report, now=NOW)
    assert result["status"] == "HOLD"
    assert "worker_b.rezon_readyz lacks an authenticated probe" in result["reason_codes"]
    assert "worker_b.rezon_healthz returned HTTP 401" not in result["reason_codes"]


def test_even_authenticated_401_is_not_ready():
    report = evidence()
    probe(report, "worker_b.rezon_readyz")["status"] = 401
    result = evaluate_preflight(report, now=NOW)
    assert "worker_b.rezon_readyz authenticated readiness returned 401" in result["reason_codes"]


def test_tags_200_without_selected_model_digest_is_not_route_qualification():
    report = evidence()
    report["model_selection"]["digest_sha256"] = ""
    result = evaluate_preflight(report, now=NOW)
    assert "selected local model digest not evidenced" in result["reason_codes"]


@pytest.mark.parametrize("mutation", [
    {"host": "worker_b"},
    {"model_tag": "nonexistent:fake"},
    {"digest_kind": "GGUF_FILE_SHA256"},
    {"digest_sha256": "b" * 64},
])
def test_selected_model_must_match_same_receipt_host_inventory(mutation):
    report = evidence()
    report["model_selection"].update(mutation)
    result = evaluate_preflight(report, now=NOW)
    assert result["status"] == "HOLD"
    assert "selected model identity absent from same-receipt host inventory" in result["reason_codes"]


def test_inventory_digests_have_distinct_types_and_reject_duplicate_or_invalid_entries():
    report = evidence()
    report["model_inventory"].append(deepcopy(report["model_inventory"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_preflight(report, now=NOW)
    report = evidence()
    report["model_inventory"][0]["digest_kind"] = "UNVERIFIED_HASH"
    with pytest.raises(ValueError, match="invalid model identity"):
        evaluate_preflight(report, now=NOW)


def test_inventory_rejects_same_host_tag_kind_with_conflicting_digests():
    report = evidence()
    conflicting = deepcopy(report["model_inventory"][0])
    conflicting["digest_sha256"] = "b" * 64
    report["model_inventory"].append(conflicting)
    with pytest.raises(ValueError, match="ambiguous duplicate model_inventory tag"):
        evaluate_preflight(report, now=NOW)


def test_paid_route_or_unknown_cost_is_not_admissible():
    for value in (None, True, "false", 0):
        report = evidence()
        report["model_selection"]["incremental_paid_compute"] = value
        result = evaluate_preflight(report, now=NOW)
        assert result["status"] == "HOLD"


def test_stale_and_future_receipts_hold():
    assert "host observations have expired" in evaluate_preflight(
        evidence(), now=NOW + timedelta(seconds=121),
    )["reason_codes"]
    assert "observation timestamp is in the future" in evaluate_preflight(
        evidence(), now=NOW - timedelta(seconds=6),
    )["reason_codes"]


def test_missing_endpoint_is_explicit_hold():
    report = evidence()
    report["observations"] = [
        obs for obs in report["observations"] if obs["id"] != "worker_b.llama_models"
    ]
    result = evaluate_preflight(report, now=NOW)
    assert "missing probe: worker_b.llama_models" in result["reason_codes"]


@pytest.mark.parametrize("mutation", [
    lambda r: r["observations"].append(deepcopy(r["observations"][0])),
    lambda r: r["observations"][0].update({"extra": "secret"}),
    lambda r: r["observations"][0].update({"kind": "HTTP"}),
    lambda r: r["observations"][0].update({"status": True}),
    lambda r: r["observations"][2].update({"credential_applied": True}),
    lambda r: r["model_selection"].update({"credential": "never serialize"}),
    lambda r: r.update({"hidden_authorization": True}),
])
def test_malformed_or_secret_bearing_receipt_rejected(mutation):
    report = evidence()
    mutation(report)
    with pytest.raises(ValueError):
        evaluate_preflight(report, now=NOW)


def test_invalid_time_and_max_age_fail_closed():
    report = evidence()
    report["observed_at_utc"] = "2026-11-05T23:00:00-05:00"
    with pytest.raises(ValueError):
        evaluate_preflight(report, now=NOW)
    with pytest.raises(ValueError):
        evaluate_preflight(evidence(), now=NOW, max_age_seconds=True)


@pytest.mark.parametrize("bad_timestamp", [
    "2026-11-05 23:00:00Z",  # permissive ISO 8601, not strict RFC3339
    "2026-11-05T23:00Z",     # required seconds missing
    "20261105T230000Z",      # compact timestamp
    "2026-11-05t23:00:00Z",  # lowercase separator
    "2026-11-05T23:00:00z",  # lowercase UTC suffix
])
def test_non_rfc3339_observation_timestamp_rejected(bad_timestamp):
    report = evidence()
    report["observed_at_utc"] = bad_timestamp
    with pytest.raises(ValueError, match="RFC3339 UTC Z"):
        evaluate_preflight(report, now=NOW)


def test_cli_is_observational_and_never_starts_runtime(tmp_path, monkeypatch, capsys):
    receipt = tmp_path / "synthetic.json"
    receipt.write_text(json.dumps(evidence()), encoding="utf-8")
    monkeypatch.setattr(
        "portal.unified_host_preflight.datetime",
        type("FixedDateTime", (datetime,), {
            "now": classmethod(lambda cls, tz=None: NOW),
        }),
    )
    assert main(["--evidence", str(receipt)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OBSERVATIONAL_PREFLIGHT_PASS"
    assert not result["deployment_authorized"]


def test_cli_bad_evidence_rejected_without_traceback(tmp_path, capsys):
    receipt = tmp_path / "bad.json"
    receipt.write_text("[", encoding="utf-8")
    assert main(["--evidence", str(receipt)]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("original,replacement", [
    ('"status":"RUNNING"', '"status":"STOPPED","status":"RUNNING"'),
    ('"incremental_paid_compute":false',
     '"incremental_paid_compute":true,"incremental_paid_compute":false'),
])
def test_cli_rejects_ambiguous_duplicate_json_keys(
    original, replacement, tmp_path, capsys, monkeypatch,
):
    raw = json.dumps(evidence(), separators=(",", ":"))
    assert original in raw
    receipt = tmp_path / "duplicate.json"
    receipt.write_text(raw.replace(original, replacement, 1), encoding="utf-8")
    monkeypatch.setattr(
        "portal.unified_host_preflight.datetime",
        type("FixedDateTime", (datetime,), {
            "now": classmethod(lambda cls, tz=None: NOW),
        }),
    )
    assert main(["--evidence", str(receipt)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("bad_payload", [
    b"[" * 2000 + b"]" * 2000,
    b" " * (32 * 1024 + 1),
    b"\xff\xfe",
])
def test_cli_malformed_deep_oversize_or_non_utf8_receipt_fails_closed(
    bad_payload, tmp_path, capsys,
):
    receipt = tmp_path / "invalid.json"
    receipt.write_bytes(bad_payload)
    assert main(["--evidence", str(receipt)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INVALID_EVIDENCE"
