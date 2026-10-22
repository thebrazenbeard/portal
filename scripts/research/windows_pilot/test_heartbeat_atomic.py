"""Source-level Windows heartbeat atomic-replace regression, no SCM or providers.

Compiles only the published atomic_json function AST, so tests cannot import
pywin32 service entrypoints or accidentally activate the installed pilot.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name("vera_mono_pilot.py")


def load_writer():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "atomic_json"]
    if len(nodes) != 1:
        raise AssertionError("exactly one production atomic_json expected")
    env = {
        "os": os, "json": json, "time": time, "tempfile": tempfile,
        "Path": Path,
        "REPLACE_RETRY_WINDOW_SECONDS": 0.12,
        "REPLACE_RETRY_INTERVAL_SECONDS": 0.01,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), env)
    return env["atomic_json"]


def windows_error(code):
    exc = PermissionError(13, "simulated Windows destination sharing failure")
    exc.winerror = code
    return exc


class AtomicHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vera-heartbeat-negative-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "heartbeat.json"
        self.write = load_writer()

    def no_temps(self):
        self.assertEqual(list(self.path.parent.glob(".heartbeat.json.*.tmp")), [])

    def test_existing_heartbeat_replaced_atomically(self):
        self.write(self.path, {"sequence": 1})
        self.write(self.path, {"sequence": 2})
        self.assertEqual(json.loads(self.path.read_text()), {"sequence": 2})
        self.no_temps()

    def test_simulated_transient_winerror5_recovers(self):
        self.write(self.path, {"sequence": 1})
        original = os.replace
        calls = []
        def flaky(src, dst):
            calls.append(1)
            if len(calls) <= 3:
                raise windows_error(5)
            return original(src, dst)
        with patch.object(os, "replace", side_effect=flaky):
            self.write(self.path, {"sequence": 2})
        self.assertEqual(len(calls), 4)
        self.assertEqual(json.loads(self.path.read_text()), {"sequence": 2})
        self.no_temps()

    def test_persistent_winerror5_fails_closed_without_erasing_prior_value(self):
        self.write(self.path, {"sequence": 1})
        start = time.monotonic()
        with patch.object(os, "replace", side_effect=windows_error(5)):
            with self.assertRaises(PermissionError):
                self.write(self.path, {"sequence": 2})
        self.assertLess(time.monotonic() - start, 0.7)
        self.assertEqual(json.loads(self.path.read_text()), {"sequence": 1})
        self.no_temps()

    def test_unrelated_error_is_not_retried(self):
        self.write(self.path, {"sequence": 1})
        with patch.object(os, "replace", side_effect=windows_error(87)) as replace:
            with self.assertRaises(PermissionError):
                self.write(self.path, {"sequence": 2})
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text()), {"sequence": 1})
        self.no_temps()

    def test_serialization_failure_keeps_prior_file_and_cleans_temp(self):
        self.write(self.path, {"sequence": 1})
        with self.assertRaises(TypeError):
            self.write(self.path, {"bad": object()})
        self.assertEqual(json.loads(self.path.read_text()), {"sequence": 1})
        self.no_temps()

    @unittest.skipUnless(os.name == "nt", "real file-open replace race is Windows-specific")
    def test_real_open_reader_lock_retries_to_completion(self):
        self.write(self.path, {"sequence": 1})
        held = self.path.open("rb")
        # Exercise the actual platform behavior before calling the candidate.
        scratch = self.path.parent / "probe.tmp"
        scratch.write_text("untrusted")
        try:
            with self.assertRaises(PermissionError):
                os.replace(scratch, self.path)
            def release():
                time.sleep(0.07)
                held.close()
            releaser = threading.Thread(target=release)
            releaser.start()
            self.write(self.path, {"sequence": 2})
            releaser.join(timeout=1)
            self.assertFalse(releaser.is_alive())
            self.assertEqual(json.loads(self.path.read_text()), {"sequence": 2})
            self.no_temps()
        finally:
            if not held.closed:
                held.close()
            scratch.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
