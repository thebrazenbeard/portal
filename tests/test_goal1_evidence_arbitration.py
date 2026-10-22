"""Tests for candidate-evidence reconciliation; not a model learning claim."""
import unittest
from goal1_evidence_arbitration import reconcile

FEATURES = {
    "train-a": "000", "train-b": "001", "train-c": "100",
    "train-d": "110", "hidden-g": "010", "hidden-h": "011",
}
def candidate_table():
    return {
        "majority": {k: str(int(v.count("1") >= 2)) for k, v in FEATURES.items()},
        "parity": {k: str(v.count("1") % 2) for k, v in FEATURES.items()},
    }
TRAIN = ("train-a", "train-b", "train-c", "train-d")
HIDDEN = ("hidden-g", "hidden-h")
def trial(observed=(), **kwargs):
    return reconcile(
        case_id="bounded-synthetic-1",
        predictions=candidate_table(),
        training_ids=TRAIN,
        holdout_ids=HIDDEN,
        observed=observed,
        **kwargs,
    )
class EvidenceArbitrationTests(unittest.TestCase):
    def test_majority_discovered_from_model_chosen_discriminating_probe(self):
        r=trial((("train-d","1","eval:majority"),))
        self.assertEqual(r.status,"ONE_SURVIVING_HYPOTHESIS")
        self.assertEqual(r.remaining,("majority",))
        self.assertEqual(dict(r.heldout_predictions),{"hidden-g":"0","hidden-h":"1"})
    def test_parity_discovered_from_same_model_chosen_probe(self):
        r=trial((("train-d","0","eval:parity"),))
        self.assertEqual(r.remaining,("parity",))
        self.assertEqual(dict(r.heldout_predictions),{"hidden-g":"1","hidden-h":"0"})
    def test_no_feedback_must_not_fabricate_a_skill(self):
        r=trial()
        self.assertEqual(r.status,"UNRESOLVED")
        self.assertEqual(r.remaining,("majority","parity"))
        self.assertEqual(dict(r.heldout_predictions),{})
        self.assertEqual(r.abstained,2)
    def test_impossible_label_marks_contradiction_not_promotion(self):
        r=trial((("train-a","1","eval:contradiction"),))
        self.assertEqual(r.status,"CONTRADICTION")
        self.assertEqual(r.remaining,())
        self.assertEqual(dict(r.heldout_predictions),{})
    def test_duplicate_evidence_is_rejected_not_reused(self):
        with self.assertRaises(ValueError):
            trial((("train-d","1","e:1"),("train-d","1","e:2")))
    def test_holdout_label_leakage_rejected(self):
        with self.assertRaises(ValueError):
            trial((("hidden-g","1","e:bad"),))
    def test_forged_precommit_rejected(self):
        with self.assertRaises(ValueError):
            trial((("train-d","1","e"),), expected_fingerprint="0"*64)
    def test_unbounded_or_invalid_labels_rejected(self):
        with self.assertRaises(ValueError):
            trial((("train-d",True,"e"),))
        with self.assertRaises(ValueError):
            trial((("train-d","1",""),))
        with self.assertRaises(ValueError):
            reconcile(case_id="x",predictions={"a":{"one":"1"}},training_ids=("one",),holdout_ids=("two",),observed=())
    def test_precommit_is_same_for_positive_negative_and_control(self):
        a=trial((("train-d","1","one"),))
        b=trial((("train-d","0","two"),))
        c=trial()
        self.assertEqual(a.fingerprint,b.fingerprint)
        self.assertEqual(a.fingerprint,c.fingerprint)
    def test_model_opinion_does_not_override_observed_label(self):
        r=trial((("train-d","1","evidence:observed"),))
        self.assertNotIn("parity",r.remaining)
        self.assertEqual(r.claim_ceiling,"SYNTHETIC_DEVELOPER_SUPPLIED_HYPOTHESIS_ARBITRATION")
if __name__=="__main__":
    unittest.main()
