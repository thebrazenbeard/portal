"""Effectless PORTAL review of a sealed synthetic metacognition experiment.

Checks evidence classification; does not certify learning, humility, sandbox
security, executed callable identity, or a selected Vera runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re


class EvidenceHold(ValueError):
    pass


ARMS = ("self_selected_toy", "frozen_zero", "guided_parity",
        "reflection_only", "random_choice", "always_abstain")
SURFACES = ("transfer", "reversal")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(text: str, label: str) -> None:
    if type(text) is not str or SHA.fullmatch(text) is None:
        raise EvidenceHold(f"{label}: invalid lowercase sha256")


def _metric(record: object, name: str) -> tuple[int, float, float | None]:
    if not isinstance(record, dict):
        raise EvidenceHold(f"{name}: missing score")
    n, errors, coverage, brier = (record.get(k) for k in
                                  ("n", "errors", "coverage", "brier_on_answered"))
    if type(n) is not int or n <= 0 or type(errors) is not int or not 0 <= errors <= n:
        raise EvidenceHold(f"{name}: invalid n/error count")
    if type(coverage) not in (float, int) or not math.isfinite(coverage) or not 0 <= coverage <= 1:
        raise EvidenceHold(f"{name}: invalid coverage")
    # Coverage is a fraction of all presented cases, not a free-standing score.
    # Allow concise decimal displays, but never accept a fractional case count.
    answered = round(n * coverage)
    if not math.isclose(n * coverage, answered, rel_tol=0.0, abs_tol=0.01):
        raise EvidenceHold(f"{name}: coverage implies a fractional answered count")
    if errors > answered:
        raise EvidenceHold(f"{name}: errors exceed answered predictions")
    if answered == 0:
        if coverage != 0 or brier is not None or errors != 0:
            raise EvidenceHold(f"{name}: no answered predictions to score")
        if not name.endswith("/always_abstain"):
            raise EvidenceHold(f"{name}: zero coverage cannot support method comparison")
    elif type(brier) not in (float, int) or not math.isfinite(brier) or not 0 <= brier <= 1:
        raise EvidenceHold(f"{name}: invalid brier")
    # Interpreting errors as thresholded binary mistakes and brier_on_answered
    # as mean squared (p - y)^2 over the SAME answered cases:
    # any wrong answer costs >= 0.25 Brier at the 0.5 threshold.
    # This is only a consistency condition on an UNTRUSTED self-report.
    if answered and errors and float(brier) + 1e-12 < 0.25 * errors / answered:
        raise EvidenceHold(f"{name}: error count incompatible with binary Brier")
    # Each threshold-CORRECT binary prediction has Brier <= .25, while
    # any threshold-WRONG prediction can contribute at most 1.0.
    # Therefore the aggregate score cannot exceed .25 + .75 * error rate.
    # As with the lower bound, this guards reported arithmetic ONLY:
    # it does not authenticate labels, scores, methods or reviewer control.
    if answered and float(brier) > 0.25 + 0.75 * errors / answered + 1e-12:
        raise EvidenceHold(f"{name}: error count incompatible with binary Brier")
    return errors, float(coverage), None if brier is None else float(brier)


def audit(*, result_file: Path, worker_file: Path, method_file: Path,
          expected_result_sha: str, expected_worker_sha: str,
          expected_method_sha: str) -> dict[str, object]:
    for label, value in (("report", expected_result_sha), ("worker", expected_worker_sha),
                         ("selection", expected_method_sha)):
        require_sha(value, label)
    # Observe each pathname exactly once: parse the very bytes that were hashed.
    # This is local snapshot consistency, NOT external source custody or
    # attestation of a later consumer's bytes.
    snapshots = {}
    for label, path, expected in (("report", result_file, expected_result_sha),
                                   ("worker", worker_file, expected_worker_sha),
                                   ("selection", method_file, expected_method_sha)):
        captured = path.read_bytes()
        if hashlib.sha256(captured).hexdigest() != expected:
            raise EvidenceHold(f"{label}: observed bytes differ from expected digest")
        snapshots[label] = captured
    report = json.loads(snapshots["report"].decode("utf-8"))
    selection = json.loads(snapshots["selection"].decode("utf-8"))
    if type(report) is not dict or type(selection) is not dict:
        raise EvidenceHold("reports must be objects")
    if report.get("runtime_qualified") is not False or report.get("independent_evaluator") is not False:
        raise EvidenceHold("synthetic report incorrectly claims runtime/independent qualification")
    if report.get("status") != "DEVELOPER_AUTHORED_SYNTHETIC_NOT_AUTONOMOUS_LEARNING":
        raise EvidenceHold("report overstates evidence class")
    if report.get("labels_exposed_to_worker") is not False or report.get("negative_claim_mismatch_rejected") is not True:
        raise EvidenceHold("reported worker isolation / mismatch test unsupported")
    if report.get("worker_sha256") != expected_worker_sha or selection.get("worker_sha256") != expected_worker_sha:
        raise EvidenceHold("worker/source identity mismatch")
    if report.get("selected") != selection.get("method") or type(selection.get("train_n")) is not int:
        raise EvidenceHold("selected method declaration mismatch")
    scores = report.get("scores")
    if not isinstance(scores, dict) or set(scores) != {
        f"{surf}/{arm}" for surf in SURFACES for arm in ARMS
    }:
        raise EvidenceHold("incomplete or unexpected arm/surface scoring inventory")
    result = {}
    for name, record in scores.items():
        result[name] = _metric(record, name)
    for surf in SURFACES:
        counts = {scores[f"{surf}/{arm}"]["n"] for arm in ARMS}
        if len(counts) != 1:
            raise EvidenceHold("different case counts across matched arms")
    ta, tb, tc = (result[f"transfer/{arm}"][0] for arm in
                  ("self_selected_toy", "frozen_zero", "guided_parity"))
    ra, rb = (result[f"reversal/{arm}"][0] for arm in
              ("self_selected_toy", "frozen_zero"))
    if result["transfer/always_abstain"][1] != 0 or result["reversal/always_abstain"][1] != 0:
        raise EvidenceHold("always-abstain control must have zero coverage")
    if result["reversal/self_selected_toy"][2] is None or result["reversal/frozen_zero"][2] is None:
        raise EvidenceHold("calibration comparison absent")
    # Descriptive SELECTIVE risk counts use reported aggregate denominators.
    # These remain self-reported, not paired case identity or utility evidence.
    descriptive = {}
    for name, record in scores.items():
        errors, coverage, brier = result[name]
        presented = record["n"]
        answered = round(presented * coverage)
        descriptive[name] = {
            "reported_presented": presented,
            "reported_answered": answered,
            "reported_abstained": presented - answered,
            "reported_coverage": coverage,
            "reported_errors_on_answered": errors,
            "reported_selective_risk": errors / answered if answered else None,
            "reported_brier_on_answered": brier,
        }

    return {
        "schema": "PORTAL_SYNTHETIC_METACOG_HUMILITY_REVIEW_V1",
        "claim_level": "HOST_SOURCE_SYNTHETIC_ONLY",
        "status": "HOLD_NOT_QUALIFIED",
        "epistemic_humility_required": True,
        "epistemic_humility_empirically_qualified": False,
        "metacognitive_self_development_qualified": False,
        "measured_source_worker_sha256": expected_worker_sha,
        "measured_result_sha256": expected_result_sha,
        "selection_declaration_digest": expected_method_sha,
        "selected_method_label": selection["method"],
        "selected_method_executable_consumption_attested": False,
        # Without per-case IDs, this is ordering of self-reported
        # aggregates, not a paired method-advantage measurement.
        "cross_arm_case_alignment": "UNVERIFIED_NO_CASE_IDS",
        "matched_arm_comparison_qualified": False,
        "reported_selective_metrics": descriptive,
        "comparative_utility_qualified": False,
        "abstention_cost_status": "UNSPECIFIED_NOT_IMPUTED",
        "reported_aggregate_ordering": {
            "self_selected_fewer_reported_transfer_errors_than_frozen": ta < tb,
            "self_selected_fewer_reported_transfer_errors_than_guided": ta < tc,
            "self_selected_more_reported_reversal_errors_than_frozen": ra > rb,
            "self_selected_higher_reported_reversal_brier_than_frozen": (
                result["reversal/self_selected_toy"][2] >
                result["reversal/frozen_zero"][2]
            ),
        },
        "always_abstain_has_zero_coverage": True,
        "hold_reasons": [
            "NO_PER_CASE_IDS_OR_MATCHED_ARM_ALIGNMENT_WITNESS",
            "NO_PRECOMMITTED_ABSTENTION_COST_OR_PAIRED_UTILITY",
            "EVALUATOR_INDEPENDENCE_SELF_REPORTED_FALSE",
            "DECLARED_METHOD_DIGEST_NOT_EXECUTABLE_INVOCATION_ATTESTATION",
            "NO_INDEPENDENT_HELDOUT_OR_TRUE_COLD_RUNTIME_RESTART",
            "ALL_ARMS_REQUIRE_EPISTEMIC_HUMILITY_INDEPENDENT_OF_SCORE",
        ],
        "authorization_effect": "NONE",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--result", required=True, type=Path)
    p.add_argument("--worker", required=True, type=Path)
    p.add_argument("--selection", required=True, type=Path)
    p.add_argument("--result-sha", required=True)
    p.add_argument("--worker-sha", required=True)
    p.add_argument("--selection-sha", required=True)
    args = p.parse_args()
    try:
        report = audit(result_file=args.result, worker_file=args.worker,
                       method_file=args.selection, expected_result_sha=args.result_sha,
                       expected_worker_sha=args.worker_sha,
                       expected_method_sha=args.selection_sha)
    except (EvidenceHold, OSError, ValueError, TypeError) as err:
        print(json.dumps({"status": "REJECTED", "reason": str(err)}, sort_keys=True))
        return 3
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())