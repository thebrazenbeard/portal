"""Fail-closed source/installation/route checks before any QVR process launch."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from observe_staged_qvr_unclean_restart import git_blob, preflight, run


class UncleanRestartPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qvr-scratch-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / ".scratch"
        self.root.mkdir()
        self.install = self.root / "installed"
        self.qvr = self.install / "vera_core/qualified_runtime.py"
        self.qvr.parent.mkdir(parents=True)
        self.qvr.write_text("test QVR bytes", encoding="utf-8")
        self.gate = self.root / "gate.py"
        self.gate.write_text('CHILD = "synthetic"', encoding="utf-8")
        file = self.qvr
        line = file.relative_to(self.install).as_posix() + " " + hashlib.sha256(
            file.read_bytes()
        ).hexdigest()
        manifest_digest = hashlib.sha256(line.encode()).hexdigest()
        self.route = self.root / "selected.json"
        self.selection = {
            "schema": "SCRATCH_QVR_SELECTED_ROUTE_V1",
            "selected": True,
            "mode": "LAB_ONLY",
            "target": "vera-mono-0.1.0-QVR",
            "source_git_blob": git_blob(self.qvr),
            "installed_files_sha256": manifest_digest,
            "install_root": str(self.install),
            "state_root": str(self.root / "disposable"),
        }
        self.save()

    def save(self):
        self.route.write_text(json.dumps(self.selection), encoding="utf-8")

    def check(self):
        return preflight(
            gate=self.gate, install=self.install, route=self.route,
            scratch=self.root, gate_blob=git_blob(self.gate),
            qvr_blob=git_blob(self.qvr),
        )

    def test_complete_lab_route_preflight(self):
        selection, program = self.check()
        self.assertEqual(program, "synthetic")
        self.assertTrue(selection["selected"])

    def test_unselected_route_denied(self):
        self.selection["selected"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.check()

    def test_production_route_denied(self):
        self.selection["mode"] = "PRODUCTION"
        self.save()
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_installed_tree_digest_denied(self):
        self.selection["installed_files_sha256"] = "f" * 64
        self.save()
        with self.assertRaises(ValueError):
            self.check()

    def test_substituted_gate_source_denied(self):
        with self.assertRaisesRegex(ValueError, "pinned gate"):
            preflight(gate=self.gate, install=self.install, route=self.route,
                      scratch=self.root, gate_blob="0" * 40,
                      qvr_blob=git_blob(self.qvr))

    def test_substituted_installed_runtime_denied(self):
        self.qvr.write_text("new QVR", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check()

    def test_symlinked_unhashed_dependency_directory_denied(self):
        outside = self.root / "unhashed-external"
        outside.mkdir()
        (outside / "arbitrary.py").write_text("print('unhashed')", encoding="utf-8")
        try:
            (self.install / "unhashed_package").symlink_to(
                outside, target_is_directory=True
            )
        except (OSError, NotImplementedError):
            self.skipTest("host denies symlink creation")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.check()

    def test_invalid_timeout_never_launches(self):
        with patch("observe_staged_qvr_unclean_restart.subprocess.Popen",
                   side_effect=AssertionError("must not launch")):
            with self.assertRaises(ValueError):
                run(gate=self.gate, install=self.install,
                    route_template=self.route, scratch=self.root,
                    gate_blob=git_blob(self.gate), qvr_blob=git_blob(self.qvr),
                    timeout=99)

    def test_scratch_marker_required(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "scratch"):
            preflight(gate=self.gate, install=self.install, route=self.route,
                      scratch=other, gate_blob=git_blob(self.gate),
                      qvr_blob=git_blob(self.qvr))


if __name__ == "__main__":
    unittest.main()
