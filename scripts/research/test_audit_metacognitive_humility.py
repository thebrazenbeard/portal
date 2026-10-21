"""Effectless negative controls for PORTAL synthetic evidence audit."""
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_metacognitive_humility as audit


class SyntheticAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.worker = self.root / "worker.py"
        self.worker.write_text("pass", encoding="utf-8")
        self.method = self.root / "method.json"
        self.method.write_text(json.dumps({"worker_sha256": audit.digest(self.worker),
                                           "method": "parity", "train_n": 20}))
        self.report = self.root / "report.json"
        score = {"n": 60, "errors": 0, "coverage": 1.0, "brier_on_answered": 0.1}
        scores = {f"{surface}/{arm}": dict(score)
                  for surface in audit.SURFACES for arm in audit.ARMS}
        for surface in audit.SURFACES:
            scores[f"{surface}/always_abstain"] = dict(
                n=60, errors=0, coverage=0.0, brier_on_answered=None)
        scores["transfer/frozen_zero"]["errors"] = 30
        scores["transfer/frozen_zero"]["brier_on_answered"] = 0.2
        scores["reversal/frozen_zero"]["errors"] = 30
        scores["reversal/frozen_zero"]["brier_on_answered"] = 0.34
        scores["reversal/self_selected_toy"]["errors"] = 60
        scores["reversal/self_selected_toy"]["brier_on_answered"] = 0.81
        self.data = {"runtime_qualified": False, "independent_evaluator": False,
                     "status": "DEVELOPER_AUTHORED_SYNTHETIC_NOT_AUTONOMOUS_LEARNING",
                     "labels_exposed_to_worker": False,
                     "negative_claim_mismatch_rejected": True,
                     "worker_sha256": audit.digest(self.worker),
                     "selected": "parity", "scores": scores}
        self.save()

    def save(self):
        self.report.write_text(json.dumps(self.data), encoding="utf-8")

    def inspect(self):
        return audit.audit(result_file=self.report, worker_file=self.worker,
                           method_file=self.method,
                           expected_result_sha=audit.digest(self.report),
                           expected_worker_sha=audit.digest(self.worker),
                           expected_method_sha=audit.digest(self.method))

    def test_synthetic_outcome_never_qualifies(self):
        result = self.inspect()
        self.assertEqual(result["status"], "HOLD_NOT_QUALIFIED")
        self.assertFalse(result["metacognitive_self_development_qualified"])
        self.assertFalse(result["epistemic_humility_empirically_qualified"])

    def test_zero_coverage_not_calibration(self):
        self.data["scores"]["reversal/always_abstain"]["coverage"] = 1
        self.save()
        with self.assertRaises(audit.EvidenceHold):
            self.inspect()

    def test_missing_arm_rejected(self):
        del self.data["scores"]["transfer/guided_parity"]
        self.save()
        with self.assertRaises(audit.EvidenceHold):
            self.inspect()

    def test_self_reported_independence_rejected(self):
        self.data["independent_evaluator"] = True
        self.save()
        with self.assertRaises(audit.EvidenceHold):
            self.inspect()

    def test_nonfinite_brier_rejected(self):
        self.data["scores"]["transfer/frozen_zero"]["brier_on_answered"] = float("nan")
        self.save()
        with self.assertRaises(audit.EvidenceHold):
            self.inspect()


    def test_errors_cannot_exceed_answered_predictions(self):
        # VN1 reviewer #6289940673: 60 errors with only 6 answered is invalid.
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=60, coverage=0.1, brier_on_answered=0.1)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "answered"):
            self.inspect()

    def test_fractional_answered_case_count_is_invalid(self):
        self.data["scores"]["transfer/self_selected_toy"]["coverage"] = 0.12
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "answered"):
            self.inspect()

    def test_zero_coverage_self_selected_not_a_learning_comparison(self):
        self.data["scores"]["transfer/self_selected_toy"].update(
            errors=0, coverage=0.0, brier_on_answered=None)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "zero coverage"):
            self.inspect()
    def test_report_snapshot_not_reread_after_hashing(self):
        from unittest.mock import patch
        # A second pathname read can select different bytes after initial hash.
        # Source metadata must parse the same captured byte snapshot as hashed.
        with patch.object(Path, "read_text",
                          side_effect=AssertionError("path opened twice")):
            result = self.inspect()
        self.assertEqual(result["status"], "HOLD_NOT_QUALIFIED")
    def test_impossible_binary_brier_with_all_errors_rejected(self):
        # VN1 #6304681161: every wrong thresholded prediction contributes >= .25 loss.
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=60, coverage=1.0, brier_on_answered=0.0)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "binary Brier"):
            self.inspect()

    def test_binary_brier_loss_floor_on_partial_errors(self):
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=30, coverage=1.0, brier_on_answered=0.12)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "binary Brier"):
            self.inspect()

    def test_impossible_zero_error_binary_brier_rejected(self):
        # A threshold-correct binary probability has squared loss <= 0.25.
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=0, coverage=1.0, brier_on_answered=0.3)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "binary Brier"):
            self.inspect()

    def test_impossible_partial_error_high_binary_brier_rejected(self):
        # With half of answers wrong: maximum mean Brier = 0.25 + .75*0.5.
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=30, coverage=1.0, brier_on_answered=0.8)
        self.save()
        with self.assertRaisesRegex(audit.EvidenceHold, "binary Brier"):
            self.inspect()

    def test_consistent_binary_brier_upper_boundary_is_allowed(self):
        # Structural consistency is not authentication or actual H/M learning.
        self.data["scores"]["transfer/self_selected_toy"].update(
            n=60, errors=30, coverage=1.0, brier_on_answered=0.625)
        self.save()
        result = self.inspect()
        self.assertEqual(result["status"], "HOLD_NOT_QUALIFIED")
    def test_aggregate_order_is_not_matched_case_comparison(self):
        # Equal counts can conceal disjoint or easier/harder test populations:
        # without evaluator-held case IDs, never claim matched-arm advantage.
        observed = self.inspect()
        self.assertEqual(observed["cross_arm_case_alignment"],
                         "UNVERIFIED_NO_CASE_IDS")
        self.assertFalse(observed["matched_arm_comparison_qualified"])
        self.assertNotIn("candidate_beats_frozen_transfer_errors", observed)
        self.assertNotIn("candidate_beats_guided_transfer_errors", observed)
        self.assertIn("NO_PER_CASE_IDS_OR_MATCHED_ARM_ALIGNMENT_WITNESS",
                      observed["hold_reasons"])
        self.assertTrue(observed["reported_aggregate_ordering"][
            "self_selected_fewer_reported_transfer_errors_than_frozen"])
if __name__ == "__main__":
    unittest.main()