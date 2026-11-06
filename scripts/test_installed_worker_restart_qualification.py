"""Offline three-process regression for installed Portal+Runner wheel coexistence.

Run with the Python interpreter from a disposable environment containing both
exact candidate wheels. The test performs no network traffic or host effects.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("installed_worker_restart_qualification.py")
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def phase(name: str, db: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--phase", name, "--db", str(db), *extra],
        capture_output=True, text=True, timeout=15, check=False,
    )


class InstalledReferenceWorkerTests(unittest.TestCase):
    def test_receipt_survives_process_boundary_and_is_not_reclaimed(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-restart-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            a = phase("prepare", db, "--target-head", HEAD_A)
            self.assertEqual(a.returncode, 0, a.stderr)
            prepared = json.loads(a.stdout)
            self.assertEqual(prepared["phase"], "PREPARED")
            b = phase("consume", db, "--fixture-observed-head", HEAD_A)
            self.assertEqual(b.returncode, 0, b.stderr)
            consumed = json.loads(b.stdout)
            self.assertEqual(consumed["receipt_class"], "SUCCEEDED")
            self.assertEqual(consumed["transport_class"], "SYNTHETIC_OFFLINE")
            c = phase("verify", db)
            self.assertEqual(c.returncode, 0, c.stderr)
            reopened = json.loads(c.stdout)
            self.assertFalse(reopened["replay_claimed"])
            self.assertEqual(prepared["route_id"], consumed["route_id"])
            self.assertEqual(consumed["route_id"], reopened["route_id"])
            self.assertEqual(consumed["receipt_sha256"], reopened["receipt_sha256"])

    def test_different_provider_head_cannot_be_promoted_to_success(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-supersession-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            self.assertEqual(phase("prepare", db, "--target-head", HEAD_A).returncode, 0)
            mismatch = phase("consume", db, "--fixture-observed-head", HEAD_B)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("SUPERSEDED", mismatch.stderr)
            verify = phase("verify", db)
            self.assertNotEqual(verify.returncode, 0)
            self.assertIn("trusted durable witness missing", verify.stderr)

    def test_manufactured_terminal_row_without_consume_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-forged-terminal-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            self.assertEqual(phase("prepare", db, "--target-head", HEAD_A).returncode, 0)
            with sqlite3.connect(db) as connection:
                connection.execute(
                    "UPDATE worker_route_outbox SET state = 'RECEIPT_RECORDED', "
                    "receipt_class = 'SUCCEEDED', receipt_sha256 = ?, "
                    "delivery_fencing_token = 99", ("z" * 64,)
                )
            verification = phase("verify", db)
            self.assertNotEqual(verification.returncode, 0)
            self.assertIn("trusted durable witness missing", verification.stderr)

    def test_forged_receipt_after_genuine_consume_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-mutated-receipt-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            self.assertEqual(phase("prepare", db, "--target-head", HEAD_A).returncode, 0)
            self.assertEqual(phase("consume", db, "--fixture-observed-head", HEAD_A).returncode, 0)
            with sqlite3.connect(db) as connection:
                connection.execute("UPDATE worker_route_outbox SET receipt_sha256 = ?, "
                                   "delivery_fencing_token = 99", ("z" * 64,))
            verification = phase("verify", db)
            self.assertNotEqual(verification.returncode, 0)
            self.assertIn("authenticated consume witness", verification.stderr)

    def test_altered_consume_witness_fails_integrity(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-mutated-witness-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            self.assertEqual(phase("prepare", db, "--target-head", HEAD_A).returncode, 0)
            self.assertEqual(phase("consume", db, "--fixture-observed-head", HEAD_A).returncode, 0)
            witness = db.with_name("qualification.consume.json")
            data = json.loads(witness.read_text())
            data["payload"]["receipt_sha256"] = "z" * 64
            witness.write_text(json.dumps(data))
            verification = phase("verify", db)
            self.assertNotEqual(verification.returncode, 0)
            self.assertIn("witness integrity failed", verification.stderr)

    def test_existing_state_refuses_implicit_reset(self):
        with tempfile.TemporaryDirectory(prefix="portal-wheel-no-reset-") as folder:
            db = Path(folder) / "qualification.sqlite3"
            self.assertEqual(phase("prepare", db, "--target-head", HEAD_A).returncode, 0)
            duplicate = phase("prepare", db, "--target-head", HEAD_A)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("refusing to overwrite", duplicate.stderr)

    def test_refuses_arbitrary_database_target_even_without_existing_state(self):
        bad = phase("prepare", Path(__file__).with_name("do-not-create.sqlite3"),
                    "--target-head", HEAD_A)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("under OS temp", bad.stderr)
        self.assertFalse(Path(__file__).with_name("do-not-create.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
