"""Host-witness negative controls; no model call and no real selected service."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from observe_ephemeral_qvr_host import git_blob, run, validate_ready

EXPECTED = "a" * 64


class HostWitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "packages/vera_core/src/vera_core/qualified_runtime.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("synthetic file", encoding="utf-8")
        self.report = {
            "phase": "initial", "pid": 123, "nonce": "nonce",
            "source_path": str(self.source), "checkpoint_digest": EXPECTED,
            "commitments": ["scratch-recovery-witness"],
            "effect_count": 0, "trust_scopes": [],
        }

    def valid(self, report):
        validate_ready(report, phase="initial", os_pid=123,
                       nonce="nonce", exact_source=self.source)

    def test_good_shape_is_only_structurally_valid(self):
        self.valid(self.report)

    def test_forged_process_identity_denied(self):
        with self.assertRaises(ValueError):
            self.valid(dict(self.report, pid=321))

    def test_nonce_replay_denied(self):
        with self.assertRaises(ValueError):
            self.valid(dict(self.report, nonce="old-run"))

    def test_substituted_source_denied(self):
        with self.assertRaises(ValueError):
            self.valid(dict(self.report, source_path=str(self.base / "other.py")))

    def test_wrong_phase_denied(self):
        with self.assertRaises(ValueError):
            self.valid(dict(self.report, phase="reopen"))

    def test_effect_and_trust_leak_denied(self):
        for change in ({"effect_count": 1}, {"trust_scopes": ["PROVIDER"]}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.valid(dict(self.report, **change))

    def test_empty_checkpoint_denied(self):
        with self.assertRaises(ValueError):
            self.valid(dict(self.report, checkpoint_digest=""))

    def test_blob_hash_changes_with_content(self):
        first = git_blob(self.source)
        self.source.write_text("replacement")
        self.assertNotEqual(first, git_blob(self.source))

    def test_wrong_exact_source_blocks_before_launch(self):
        with patch("observe_ephemeral_qvr_host._child",
                   side_effect=AssertionError("must not launch")):
            with self.assertRaises(ValueError):
                run(self.base, self.base, "0" * 40)

    def test_no_parent_denied(self):
        with self.assertRaises(ValueError):
            run(self.base, self.base / "not-present", git_blob(self.source))

    def test_bad_timeouts_denied(self):
        with self.assertRaises(ValueError):
            run(self.base, self.base, git_blob(self.source), timeout=35)


if __name__ == "__main__":
    unittest.main()
