import unittest
from cross_lane_evidence_gate import assess_claim, assess_metacognition_comparison


class CrossLaneGateTests(unittest.TestCase):
    def test_empty_source_claim_is_rejected(self):
        r = assess_claim("SOURCE", {})
        self.assertEqual(r.status, "HOLD_MISSING_EVIDENCE")
        self.assertEqual(r.missing, ("source_digest",))

    def test_self_asserted_full_chain_never_qualifies(self):
        from cross_lane_evidence_gate import REQUIRED
        claims = {key: "forged" for keys in REQUIRED.values() for key in keys}
        r = assess_claim("AUTHORIZED_OPERATION", claims)
        self.assertEqual(r.status, "HOLD_EXTERNAL_VERIFICATION")
        self.assertIn("CALLER_CONTROLLED_FIELDS_NOT_AUTHENTICATED", r.limitations)

    def test_missing_intermediate_stage_blocks_later_stage(self):
        r = assess_claim("RUNTIME_CONSUMPTION", {
            "source_digest": "a", "build_source_digest": "a",
            "build_artifact_digest": "b", "installed_artifact_digest": "b",
            "consumer_observation_digest": "c",
        })
        self.assertEqual(r.structurally_supported_through, "INSTALLED_ARTIFACT")
        self.assertIn("route_target_digest", r.missing)

    def test_unknown_stage_fails_closed(self):
        with self.assertRaises(ValueError):
            assess_claim("CONSCIOUSNESS", {})

    def test_shuffled_case_ids_hold(self):
        a = {"case_ids": ["A", "B"], "presented": 2, "answered": 1,
             "abstained": 1, "source_group": "g", "resource_budget": 10}
        b = dict(a, case_ids=["B", "A"])
        self.assertIn("UNMATCHED_CASE_ORDER_OR_IDS",
                      assess_metacognition_comparison({"candidate": a, "control": b})[1])

    def test_abstention_denominator_rejected(self):
        a = {"case_ids": ["A", "B"], "presented": 2, "answered": 0,
             "abstained": 0, "source_group": "g", "resource_budget": 10}
        self.assertIn("candidate:INCOHERENT_COVERAGE",
                      assess_metacognition_comparison({"candidate": a, "control": a})[1])

    def test_clean_self_report_still_unqualified(self):
        a = {"case_ids": ["A"], "presented": 1, "answered": 1,
             "abstained": 0, "source_group": "g", "resource_budget": 10}
        self.assertEqual(assess_metacognition_comparison(
            {"candidate": a, "control": a}),
            ("HOLD", ("INDEPENDENT_LABEL_CUSTODY_AND_CAUSAL_USE_UNVERIFIED",)))


if __name__ == "__main__":
    unittest.main()
