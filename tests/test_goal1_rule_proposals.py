"""Source-only Goal 1 tests: model proposals are bounded rules, not answer tables."""
import json
import unittest
from goal1_rule_proposals import commit_rules, predict_rules

MODEL = "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
MODEL_RESPONSE = '{"candidates":["parity_ones","at_least_ones:2","bit_is_one:0","bit_is_one:1"]}'


class RuleProposalTests(unittest.TestCase):
    def commit(self, response=MODEL_RESPONSE):
        return commit_rules(case_id="synthetic-rule-1", model_digest=MODEL, response=response)

    def test_actual_qwen_proposal_is_bounded_and_committed(self):
        x = self.commit()
        self.assertEqual(x.rules, ("parity_ones", "at_least_ones:2", "bit_is_one:0", "bit_is_one:1"))
        self.assertEqual(len(x.commitment_sha256), 64)
        self.assertEqual(x.source_kind, "UNVERIFIED_LOCAL_MODEL_OUTPUT")

    def test_proposal_digest_does_not_depend_on_hidden_cases(self):
        x = self.commit()
        self.assertEqual(
            x.commitment_sha256,
            self.commit().commitment_sha256,
        )
        first = predict_rules(x, {"future-a": "010"}, expected_commitment=x.commitment_sha256)
        other = predict_rules(x, {"future-b": "111"}, expected_commitment=x.commitment_sha256)
        self.assertEqual(first["parity_ones"], {"future-a": "1"})
        self.assertEqual(other["at_least_ones:2"], {"future-b": "1"})
        self.assertEqual(x.commitment_sha256, self.commit().commitment_sha256)

    def test_generalization_without_lookup_table(self):
        x = self.commit()
        p = predict_rules(x, {"unknown-1": "101", "unknown-2": "000"}, expected_commitment=x.commitment_sha256)
        self.assertEqual(p["parity_ones"], {"unknown-1": "0", "unknown-2": "0"})
        self.assertEqual(p["at_least_ones:2"], {"unknown-1": "1", "unknown-2": "0"})
        self.assertEqual(p["bit_is_one:0"], {"unknown-1": "1", "unknown-2": "0"})

    def test_untrusted_code_cannot_run(self):
        with self.assertRaises(ValueError):
            self.commit(json.dumps({"candidates": ["parity_ones", "__import__('os').system('echo unsafe')"]}))
        with self.assertRaises(ValueError):
            self.commit(json.dumps({"candidates": ["parity_ones", "at_least_ones:2;import sys"]}))

    def test_invalid_or_duplicate_json_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            self.commit('{"candidates":["parity_ones","at_least_ones:2"],"candidates":["parity_ones","bit_is_one:0"]}')
        with self.assertRaises(ValueError):
            self.commit('{"candidates":["parity_ones","at_least_ones:2"],"answer":"hidden-a=0"}')

    def test_duplicate_empty_or_excess_rules_rejected(self):
        for rules in ([], ["parity_ones"], ["parity_ones", "parity_ones"], ["parity_ones"] * 20):
            with self.subTest(rules=rules):
                with self.assertRaises(ValueError):
                    self.commit(json.dumps({"candidates": rules}))

    def test_wrong_commitment_cannot_be_reused(self):
        x = self.commit()
        with self.assertRaises(ValueError):
            predict_rules(x, {"future-a": "010"}, expected_commitment="0" * 64)
        y = self.commit('{"candidates":["parity_ones","at_least_ones:2"]}')
        self.assertNotEqual(y.commitment_sha256, x.commitment_sha256)

    def test_invalid_binary_features_fail_closed(self):
        x = self.commit()
        for feature in ("1111", "01", "12a", "0000000000000000000"):
            with self.subTest(feature=feature):
                with self.assertRaises(ValueError):
                    predict_rules(x, {"future-a": feature}, expected_commitment=x.commitment_sha256)

    def test_rejects_malformed_model_provenance(self):
        with self.assertRaises(ValueError):
            commit_rules(case_id="synthetic-rule-1", model_digest="qwen3:4b", response=MODEL_RESPONSE)
        with self.assertRaises(ValueError):
            commit_rules(case_id="../escape", model_digest=MODEL, response=MODEL_RESPONSE)
        with self.assertRaises(ValueError):
            commit_rules(case_id="synthetic-rule-1", model_digest=MODEL, response=MODEL_RESPONSE * 1000)

    def test_rejects_uncommitted_hypothesis_mutation(self):
        x = self.commit()
        with self.assertRaises((AttributeError, TypeError)):
            x.rules = ("parity_ones", "exact_ones:1")
        with self.assertRaises(ValueError):
            predict_rules(x, {"hidden": "101"}, expected_commitment=None)


if __name__ == "__main__":
    unittest.main()
