"""Fail-closed provisional skill validation after independent synthetic holdout."""
import unittest
from dataclasses import replace

from goal1_blind_inquiry_gate import BlindInquiryGate, TrialClosed
from goal1_evidence_arbitration import reconcile
from goal1_rule_proposals import commit_rules, predict_rules
from goal1_provisional_skill_validation import validate_candidate

MODEL = "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
OUTPUT = '{"candidates":["parity_ones","at_least_ones:2","bit_is_one:0","bit_is_one:1"]}'
TRAIN = {"train-a":"000", "train-b":"001", "train-c":"100", "train-d":"110", "train-e":"101"}
HOLDOUT = {"hidden-g":"010","hidden-h":"111"}


class OpenWorldTests(unittest.TestCase):
    def setUp(self):
        self.locked = commit_rules(case_id="g1-open-world",model_digest=MODEL,response=OUTPUT)
        self.predict = predict_rules(self.locked,{**TRAIN,**HOLDOUT},
                    expected_commitment=self.locked.commitment_sha256)

    def arbitration(self, observed):
        return reconcile(case_id="g1-open-world",predictions=self.predict,
             training_ids=tuple(TRAIN),holdout_ids=tuple(HOLDOUT),observed=observed)

    def validator(self, labels):
        features={"training-sentinel":"000", **HOLDOUT}
        answers={"training-sentinel":"0", **labels}
        return BlindInquiryGate(case_id="g1-validation",inputs=features,
               answers=answers,training_ids=("training-sentinel",),
               holdout_ids=tuple(HOLDOUT),probe_budget=0)

    def check(self, r, v, **kwargs):
        return validate_candidate(commitment=self.locked,arbitration=r,
            validation_gate=v,expected_commitment=self.locked.commitment_sha256,
            expected_arbitration_fingerprint=r.fingerprint,**kwargs)

    def test_in_family_rule_passes_noncanonical_validation(self):
        r=self.arbitration((("train-e","1","e:1"),("train-d","1","e:2"),("train-c","0","e:3")))
        self.assertEqual(r.remaining,("at_least_ones:2",))
        result=self.check(r,self.validator({"hidden-g":"0","hidden-h":"1"}))
        self.assertTrue(result.eligible_for_noncanonical_retention)
        self.assertFalse(result.qualified_as_self_learning)
        self.assertEqual(result.validated_correct,2)
        self.assertEqual(result.validated_attempted,2)

    def test_out_of_family_true_rule_fails_even_with_single_survivor(self):
        r=self.arbitration((("train-b","1","e:out-of-family"),))
        self.assertEqual(r.remaining,("parity_ones",))
        result=self.check(r,self.validator({"hidden-g":"1","hidden-h":"0"}))
        self.assertEqual(result.status,"REJECTED_BY_NEW_EVIDENCE")
        self.assertFalse(result.eligible_for_noncanonical_retention)
        self.assertEqual(result.validated_correct,1)

    def test_absent_feedback_does_not_promote_any_rule(self):
        r=self.arbitration(())
        gate=self.validator({"hidden-g":"0","hidden-h":"1"})
        result=self.check(r,gate)
        self.assertEqual(result.status,"NO_UNIQUE_RULE")
        self.assertFalse(result.eligible_for_noncanonical_retention)
        self.assertFalse(gate.closed)

    def test_validation_label_probe_is_not_allowed(self):
        r=self.arbitration((("train-b","1","e:1"),))
        gate=BlindInquiryGate(case_id="g1-validation",
                inputs={"sentinel":"000", "hidden-g":"010","hidden-h":"111"},
                answers={"sentinel":"0","hidden-g":"1","hidden-h":"0"},
                training_ids=("sentinel",),holdout_ids=("hidden-g","hidden-h"),probe_budget=1)
        gate.probe("sentinel")
        with self.assertRaises(ValueError):
            self.check(r,gate)

    def test_repeated_validation_is_refused(self):
        r=self.arbitration((("train-b","1","e:1"),))
        gate=self.validator({"hidden-g":"1","hidden-h":"0"})
        self.check(r,gate)
        with self.assertRaises(TrialClosed):
            self.check(r,gate)

    def test_wrong_commitment_cannot_validate(self):
        r=self.arbitration((("train-b","1","e:1"),))
        gate=self.validator({"hidden-g":"1","hidden-h":"0"})
        with self.assertRaises(ValueError):
            validate_candidate(commitment=self.locked,arbitration=r,
                   validation_gate=gate,expected_commitment="0"*64,
                   expected_arbitration_fingerprint=r.fingerprint)
        self.assertFalse(gate.closed)

    def test_forged_arbitration_commitment_rejected(self):
        r=self.arbitration((("train-b","1","e:1"),))
        gate=self.validator({"hidden-g":"1","hidden-h":"0"})
        with self.assertRaises(ValueError):
            validate_candidate(commitment=self.locked,arbitration=r,
                   validation_gate=gate,expected_commitment=self.locked.commitment_sha256,
                   expected_arbitration_fingerprint="0"*64)
        self.assertFalse(gate.closed)

    def test_validation_cannot_turn_a_hypothesis_into_general_agi_claim(self):
        r=self.arbitration((("train-b","1","e:1"),))
        gate=self.validator({"hidden-g":"1","hidden-h":"1"})
        out=self.check(r,gate)
        self.assertEqual(out.claim_ceiling,
            "SYNTHETIC_SEPARATE_HOLDOUT_VALIDATION_NOT_INDEPENDENT_QUALIFICATION")
        self.assertFalse(out.qualified_as_self_learning)


if __name__=="__main__":
    unittest.main()
