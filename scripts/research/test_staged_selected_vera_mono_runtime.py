"""Negative-only regression cases for the opt-in scratch-selected QVR host gate.

These tests never install Vera, import QVR, or start a resident process.
Actual positive host observation requires an existing isolated package installation.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("stage_selected_vera_mono_runtime.py")


def child_source() -> str:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHILD" for t in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise ValueError("child program not present")


class SelectedGateNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qvr-gate-negative-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "stub-install"
        self.install.mkdir()
        (self.install / "stub.txt").write_text("not Vera", encoding="utf-8")

    def reject(self, changes: dict, expected: str) -> None:
        state = self.root / "must-not-exist"
        route = {
            "schema": "SCRATCH_QVR_SELECTED_ROUTE_V1",
            "selected": True,
            "mode": "LAB_ONLY",
            "target": "vera-mono-0.1.0-QVR",
            "install_root": str(self.install),
            "state_root": str(state),
            "source_git_blob": "0" * 40,
            "installed_files_sha256": "0" * 64,
        }
        route.update(changes)
        candidate = self.root / "route.json"
        candidate.write_text(json.dumps(route), encoding="utf-8")
        outcome = subprocess.run(
            [sys.executable, "-B", "-u", "-c", child_source(),
             str(candidate), "first"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn(expected, outcome.stderr)
        self.assertFalse(state.exists(), "invalid route created state")

    def test_unselected_route_fails_before_memory(self):
        self.reject({"selected": False}, "no exact selected lab route")

    def test_wrong_target_fails_before_memory(self):
        self.reject({"target": "unrelated"}, "no exact selected lab route")

    def test_nonlab_mode_fails_before_memory(self):
        self.reject({"mode": "PRODUCTION"}, "no exact selected lab route")

    def test_forged_install_digest_fails_before_memory(self):
        self.reject({}, "installed artifact digest does not match selected route")

    def test_wrong_schema_fails_before_memory(self):
        self.reject({"schema": "OTHER"}, "no exact selected lab route")


if __name__ == "__main__":
    unittest.main()
