"""Native Linux synthetic ext2 stale-stage / stale-packed-artifact falsifier."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
SPEC = importlib.util.spec_from_file_location(
    "portal_packed_verify", ROOT / "tools/qualification/verify_packed_ext2_member_lock.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
MEMBERS = {
    "/opt/veraos/components/vera_mono/core.py": b"stage-verified-mono-v1\n",
    "/opt/veraos/components/portal/entry.py": b"stage-verified-portal-v1\n",
}

def populate(stage, items):
    for name, body in items.items():
        p = stage / name.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)

def mkext2(stage, image):
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext2", "-d",
                    str(stage), str(image), "8192"],
                   check=True, capture_output=True, timeout=45)

class PackedImageMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("mke2fs") or not shutil.which("debugfs"):
            raise unittest.SkipTest("requires native Linux mke2fs and debugfs")
        source = os.environ.get("VERAOS40_ROOT")
        if not source:
            raise unittest.SkipTest("provide exact detached VeraOS#40 source")
        cls.veraos = Path(source)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        populate(self.stage, MEMBERS)
        self.image = self.root / "fixture.ext2"
        mkext2(self.stage, self.image)
        self.lock = {"schema": CHECK.SCHEMA, "kind": "SYNTHETIC_DISPOSABLE_EXT2",
                     "image_sha256": CHECK.sha(self.image.read_bytes()),
                     "members": {p: CHECK.sha(b) for p, b in MEMBERS.items()}}

    def check(self):
        return CHECK.verify(self.stage, self.image, self.lock, self.veraos)

    def test_audited_stage_then_mutated_stage_or_packed_image_holds(self):
        fresh = self.check()
        self.assertEqual(fresh["status"], "PASS_SYNTHETIC_ONLY", fresh)
        print("BASELINE", json.dumps(fresh, sort_keys=True))
        path = self.stage / "opt/veraos/components/vera_mono/core.py"
        path.write_bytes(b"changed stage AFTER audit\n")
        stale_stage = self.check()
        self.assertEqual(stale_stage["status"], "HOLD_STALE_ARTIFACT")
        self.assertIn("stale or mismatched", " ".join(stale_stage["issues"]))
        print("MUTATED_STAGE", json.dumps(stale_stage, sort_keys=True))
        path.write_bytes(MEMBERS["/opt/veraos/components/vera_mono/core.py"])
        adversarial = self.root / "postpack-stage"
        adversarial.mkdir()
        replacement = dict(MEMBERS)
        replacement["/opt/veraos/components/portal/entry.py"] = b"changed packed member\n"
        populate(adversarial, replacement)
        new_image = self.root / "replacement.ext2"
        mkext2(adversarial, new_image)
        new_image.replace(self.image)
        stale_packed = self.check()
        self.assertEqual(stale_packed["status"], "HOLD_STALE_ARTIFACT")
        self.assertNotEqual(fresh["image_sha256_before"],
                            stale_packed["image_sha256_before"])
        self.assertIn("stale or mismatched", " ".join(stale_packed["issues"]))
        print("MUTATED_PACKED", json.dumps(stale_packed, sort_keys=True))
        self.assertFalse(stale_packed["guest_boot_verified"])
        self.assertFalse(stale_packed["runtime_qualified"])

    def test_source_cut_and_kind_do_not_silently_change(self):
        copy = dict(self.lock)
        copy["kind"] = "REAL_BUILDROOT_OUTPUT"
        with self.assertRaises(CHECK.Hold):
            CHECK.verify(self.stage, self.image, copy, self.veraos)
        copy = dict(self.lock)
        copy["members"] = {"/opt/veraos/../wrong": CHECK.sha(b"x")}
        with self.assertRaises(CHECK.Hold):
            CHECK.verify(self.stage, self.image, copy, self.veraos)

if __name__ == "__main__":
    unittest.main(verbosity=2)

