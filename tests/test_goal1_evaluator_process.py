"""Process-separated Goal1 scorer, no actual model or independently sealed custody."""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "research" / "goal1_evaluator_process.py"


class EvaluatorProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vera-goal1-eval-")
        self.addCleanup(self.tmp.cleanup)
        self.fixture = pathlib.Path(self.tmp.name) / "evaluator-secret.json"
        # Ground truth exists only in the evaluator fixture, not in commands.
        self.fixture.write_text(json.dumps({
            "case_id": "separated-trial-1",
            "inputs": {"train-a": "000", "train-b": "001", "hidden-c": "010", "hidden-d": "111"},
            "answers": {"train-a": "0", "train-b": "1", "hidden-c": "1", "hidden-d": "1"},
            "training_ids": ["train-a", "train-b"],
            "holdout_ids": ["hidden-c", "hidden-d"],
            "probe_budget": 1,
        }), encoding="utf-8")
        self.p = subprocess.Popen(
            [sys.executable, "-B", str(SOURCE), "--fixture", str(self.fixture)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.addCleanup(self.shutdown)

    def shutdown(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(timeout=3)
        self.p.stdout.close()
        self.p.stderr.close()

    def message(self, data):
        self.p.stdin.write(json.dumps(data, separators=(",", ":")) + "\n")
        self.p.stdin.flush()
        response = self.p.stdout.readline()
        self.assertTrue(response, "evaluator ended unexpectedly")
        return json.loads(response)

    def test_initial_surface_has_no_targets_or_hidden_features(self):
        reply = self.message({"action": "start"})
        self.assertEqual(reply["status"], "INQUIRY")
        self.assertIn("train-a", reply["prompt"])
        self.assertNotIn("hidden-c", reply["prompt"])
        self.assertNotIn("answers", reply)
        self.assertNotIn("truth", reply)
        self.assertNotIn("fixture_path", reply)
        self.assertNotIn("commitment", reply)  # short labels are guessable from hashes
        self.assertNotEqual(self.p.pid, __import__("os").getpid())

    def test_learner_queries_one_case_then_cannot_query_again(self):
        self.message({"action": "start"})
        outcome = self.message({"action": "probe", "item": "train-b"})
        self.assertEqual(outcome, {"status": "INQUIRY", "item": "train-b", "features": "001", "label": "1"})
        repeated = self.message({"action": "probe", "item": "train-b"})
        self.assertEqual(repeated["error"], "PROBE_DENIED")
        budget = self.message({"action": "probe", "item": "train-a"})
        self.assertEqual(budget["error"], "PROBE_DENIED")
        holdout = self.message({"action": "probe", "item": "hidden-c"})
        self.assertEqual(holdout["error"], "PROBE_DENIED")

    def test_holdout_inputs_only_and_single_all_case_score(self):
        self.message({"action": "start"})
        self.message({"action": "probe", "item": "train-a"})
        hold = self.message({"action": "open_holdout"})
        self.assertEqual(hold["inputs"], {"hidden-c": "010", "hidden-d": "111"})
        self.assertNotIn("answers", hold)
        self.assertEqual(self.message({"action": "probe", "item": "train-b"})["error"], "PROBE_DENIED")
        final = self.message({"action": "submit", "predictions": {"hidden-c": "0", "hidden-d": "1"}})
        self.assertEqual(final["score"]["correct"], 1)
        self.assertEqual(final["score"]["failed"], 1)
        self.assertEqual(final["score"]["attempted"], 2)
        self.assertEqual(self.message({"action": "submit", "predictions": {"hidden-c": "1", "hidden-d": "1"}})["error"], "TRIAL_CLOSED")

    def test_invalid_submission_does_not_change_score_or_open_labels(self):
        self.message({"action": "start"})
        self.message({"action": "open_holdout"})
        invalid = self.message({"action": "submit", "predictions": {"hidden-c": "1"}})
        self.assertEqual(invalid["error"], "INVALID_ARGUMENT")
        final = self.message({"action": "submit", "predictions": {"hidden-c": "1", "hidden-d": "1"}})
        self.assertEqual(final["score"]["correct"], 2)

    def test_malformed_requests_are_bounded_fail_closed(self):
        self.message({"action": "start"})
        self.assertEqual(self.message({"action": "probe", "item": "hidden-c", "debug": "reveal"})["error"], "INVALID_REQUEST")
        self.assertEqual(self.message({"action": "truth"})["error"], "INVALID_REQUEST")
        self.assertEqual(self.message({"action": "probe", "item": "train-a"})["label"], "0")

    def test_no_probe_has_no_public_truth_commitment(self):
        hello = self.message({"action": "start"})
        self.assertNotIn("fixture_hash", json.dumps(hello))
        self.assertNotIn("truth", json.dumps(hello))
        open_result = self.message({"action": "open_holdout"})
        self.assertEqual(set(open_result), {"status", "inputs"})


if __name__ == "__main__":
    unittest.main()
