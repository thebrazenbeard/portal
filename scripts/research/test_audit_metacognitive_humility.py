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


if __name__ == "__main__":
    unittest.main()