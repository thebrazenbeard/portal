"""Frozen VeraOS #40 synthetic ext2 stage-to-packed freshness negatives.

This module NEVER establishes a Buildroot-produced image or an installed VeraOS.
Pass VERAOS40_AUDIT_PATH pointing to the unchanged Audit-VeraOSImage.py at
Git blob 60a914ba06d71709f38c23236c2c52b33724b017.
"""
from __future__ import annotations

import ast
import hashlib
import re
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

OS40_BLOB = "60a914ba06d71709f38c23236c2c52b33724b017"
MEMBERS = {
    "/opt/veraos/components/vera_mono/probe.py": b"resident v1\n",
    "/opt/veraos/components/portal/route.py": b"route v1\n",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_os40_reader():
    source = os.environ.get("VERAOS40_AUDIT_PATH")
    if not source:
        raise unittest.SkipTest("requires exact VeraOS #40 reader path")
    data = Path(source).read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != OS40_BLOB:
        raise AssertionError(f"VeraOS #40 source blob mismatch: {blob}")
    # Extract exact unchanged class AST; avoid importing unrelated VeraOS modules.
    parsed = ast.parse(data, filename=source)
    nodes = [node for node in parsed.body
             if isinstance(node, ast.ClassDef)
             and node.name in {"AuditError", "ExtRootfs"}]
    if len(nodes) != 2 or [n.name for n in nodes] != ["AuditError", "ExtRootfs"]:
        raise AssertionError("VeraOS #40 reader ABI missing")
    namespace = {"Path": Path, "re": re, "subprocess": subprocess}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), source, "exec"), namespace)
    return namespace["ExtRootfs"]


def staged_receipt(stage: Path) -> dict[str, str]:
    return {p: digest(stage / p.lstrip("/")) for p in MEMBERS}


def verify_fresh_packed_image(stage: Path, image: Path, receipt: dict[str, str],
                              expected_image_sha: str, reader_type) -> dict:
    """Verify CURRENT named image and staged files; cannot reserve later consumers."""
    before = digest(image)
    if before != expected_image_sha:
        raise AssertionError("whole packed ext2 identity mismatch")
    reader = reader_type(image)
    observed = reader.component_files()
    expected = {"components/" + p.split("/components/", 1)[1] for p in receipt}
    if observed != expected:
        raise AssertionError("packed ext2 source inventory mismatch")
    member_digests = {}
    for name, expected_sha in sorted(receipt.items()):
        if digest(stage / name.lstrip("/")) != expected_sha:
            raise AssertionError("staged source changed since frozen receipt")
        packed_bytes = reader.read(name)
        if packed_bytes is None:
            raise AssertionError("packed ext2 member missing")
        packed_sha = hashlib.sha256(packed_bytes).hexdigest()
        if packed_sha != expected_sha:
            raise AssertionError("packed ext2 member differs from staged source")
        member_digests[name] = packed_sha
    if digest(image) != before:
        raise AssertionError("packed ext2 changed during verification")
    return {"image_sha256": before, "member_sha256": member_digests,
            "artifact_kind": "SYNTHETIC_EXT2_NOT_BUILDROOT"}


class TestSourceToPackedExt2Freshness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if any(shutil.which(c) is None for c in ("mke2fs", "debugfs")):
            raise unittest.SkipTest("mke2fs and debugfs needed")
        cls.reader_type = original_os40_reader()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="portal-synthetic-packed-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage = self.root / "staged"
        self.image = self.root / "SYNTHETIC-NOT-VERAOS.ext2"
        for name, contents in MEMBERS.items():
            target = self.stage / name.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        self.receipt = staged_receipt(self.stage)
        subprocess.run(["mke2fs", "-q", "-F", "-t", "ext2", "-d", str(self.stage),
                        str(self.image), "4096"], check=True, capture_output=True,
                       timeout=30)
        self.first_image_sha = digest(self.image)
        print("SYNTHETIC_EXT2_INITIAL", self.first_image_sha,
              "BYTES", self.image.stat().st_size, "PID", os.getpid(), flush=True)

    def check(self):
        return verify_fresh_packed_image(
            self.stage, self.image, self.receipt, self.first_image_sha, self.reader_type)

    def test_initial_stage_equals_fresh_packed_ext2(self):
        result = self.check()
        self.assertEqual(result["artifact_kind"], "SYNTHETIC_EXT2_NOT_BUILDROOT")
        self.assertEqual(len(result["member_sha256"]), len(MEMBERS))
        self.assertEqual(result["image_sha256"], self.first_image_sha)

    def test_equal_length_stage_mutation_after_audit_rejected(self):
        self.check()
        old_sha = digest(self.image)
        target = self.stage / "opt/veraos/components/vera_mono/probe.py"
        target.write_bytes(b"resident v2\n")
        with self.assertRaisesRegex(AssertionError, "staged source changed"):
            self.check()
        self.assertEqual(digest(self.image), old_sha)
        print("STAGED_MUTATION_IMAGE_UNCHANGED", old_sha,
              "NEW_STAGE_SHA", digest(target), flush=True)

    def test_packed_member_mutation_after_audit_rejected(self):
        self.check()
        replaced = self.root / "replacement.py"
        replaced.write_bytes(b"packed altered\n")
        member = "/opt/veraos/components/vera_mono/probe.py"
        for command in ("rm " + member, "write " + str(replaced) + " " + member):
            subprocess.run(["debugfs", "-w", "-R", command, str(self.image)],
                           check=True, capture_output=True, timeout=20)
        self.assertNotEqual(self.reader_type(self.image).read(member), MEMBERS[member])
        self.assertNotEqual(digest(self.image), self.first_image_sha)
        print("PACKED_MEMBER_MUTATION", "BEFORE", self.first_image_sha,
              "AFTER", digest(self.image), "MEMBER_SHA",
              hashlib.sha256(self.reader_type(self.image).read(member)).hexdigest(), flush=True)
        with self.assertRaisesRegex(AssertionError, "whole packed ext2 identity mismatch"):
            self.check()

    def test_whole_image_metadata_tamper_rejected(self):
        self.check()
        with self.image.open("r+b") as f:
            f.seek(-1, os.SEEK_END)
            last = f.read(1)
            f.seek(-1, os.SEEK_END)
            f.write(bytes([last[0] ^ 1]))
        self.assertNotEqual(digest(self.image), self.first_image_sha)
        print("PACKED_WHOLE_IMAGE_TAMPER", "BEFORE", self.first_image_sha,
              "AFTER", digest(self.image), flush=True)
        with self.assertRaisesRegex(AssertionError, "whole packed ext2 identity mismatch"):
            self.check()


if __name__ == "__main__":
    unittest.main(verbosity=2)
