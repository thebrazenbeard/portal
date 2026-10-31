"""No-effect developer-owned tests of a subject-blind inquiry scorer.

These tests use a synthetic hidden binary rule. They do not invoke Vera,
QualifiedVeraRuntime or a local language model and do not measure learning.
"""
import unittest
import sys
from pathlib import Path

REPO_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "research"
sys.path.insert(0, str(REPO_SOURCE if REPO_SOURCE.is_dir()
                       else Path(__file__).resolve().parent))
from goal1_blind_inquiry_gate import BlindInquiryGate, ProbeDenied, TrialClosed


class BlindInquiryGateTests(unittest.TestCase):
    def setUp(self):
        # Evaluator-owned truth. The subject receives only a rendered prompt.
        self.gate = BlindInquiryGate(
            case_id='r1',
            inputs={'train-a': '00', 'train-b': '01', 'train-c': '10',
                    'hidden-d': '11', 'hidden-e': '111'},
            answers={'train-a': '0', 'train-b': '1', 'train-c': '1',
                     'hidden-d': '0', 'hidden-e': '1'},
            training_ids=('train-a', 'train-b', 'train-c'),
            holdout_ids=('hidden-d', 'hidden-e'),
            probe_budget=2,
        )

    def test_public_prompt_does_not_expose_answers_or_holdout_identifiers(self):
        prompt = self.gate.public_prompt()
        self.assertIn('train-a', prompt)
        self.assertNotIn('hidden-d', prompt)
        self.assertNotIn('hidden-e', prompt)
        self.assertNotIn('answers', prompt.lower())

    def test_model_selects_probe_and_evaluator_only_reveals_selected_outcome(self):
        self.assertEqual(self.gate.probe('train-a'), ('00', '0'))
        self.assertEqual(self.gate.probe('train-c'), ('10', '1'))
        with self.assertRaises(ProbeDenied):
            self.gate.probe('train-b')
        with self.assertRaises(ProbeDenied):
            self.gate.probe('hidden-d')
        self.assertEqual(self.gate.probes_used, 2)

    def test_precommit_predictions_and_single_final_score(self):
        self.gate.probe('train-b')
        heldout = self.gate.open_holdout()
        self.assertEqual(heldout, {'hidden-d': '11', 'hidden-e': '111'})
        score = self.gate.commit_predictions({'hidden-d': '0', 'hidden-e': '1'})
        self.assertEqual(score['correct'], 2)
        self.assertEqual(score['attempted'], 2)
        self.assertEqual(score['probes_used'], 1)
        self.assertEqual(score['claim_ceiling'],
                         'DEVELOPER_HELD_SYNTHETIC_ORACLE_NOT_MODEL_LEARNING')
        with self.assertRaises(TrialClosed):
            self.gate.commit_predictions({'hidden-d': '1', 'hidden-e': '1'})
        with self.assertRaises(TrialClosed):
            self.gate.probe('train-a')

    def test_invalid_submission_does_not_leak_or_mutate(self):
        self.gate.open_holdout()
        with self.assertRaises(ValueError):
            self.gate.commit_predictions({'hidden-d': '0'})
        self.assertEqual(self.gate.probes_used, 0)
        self.assertFalse(self.gate.closed)
        with self.assertRaises(ValueError):
            self.gate.commit_predictions({'hidden-d': 'x', 'hidden-e': '1'})

    def test_holdout_open_prevents_further_training_probes(self):
        self.gate.open_holdout()
        with self.assertRaises(ProbeDenied):
            self.gate.probe('train-c')

    def test_submission_before_holdout_open_rejected(self):
        with self.assertRaises(ValueError):
            self.gate.commit_predictions({'hidden-d': '0', 'hidden-e': '1'})

    def test_duplicate_probe_cannot_give_free_further_information(self):
        self.gate.probe('train-a')
        with self.assertRaises(ProbeDenied):
            self.gate.probe('train-a')
        self.assertEqual(self.gate.probes_used, 1)

    def test_all_attempts_count_and_cannot_drop_failed_cases(self):
        self.gate.open_holdout()
        score = self.gate.commit_predictions({'hidden-d': '1', 'hidden-e': '1'})
        self.assertEqual(score['correct'], 1)
        self.assertEqual(score['failed'], 1)
        self.assertEqual(score['attempted'], 2)


if __name__ == '__main__':
    unittest.main()
