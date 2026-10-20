"""Synthetic unit tests; do not characterize these as actual guest qualification."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_veraos_image_selection import ArtifactError, inspect_image


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "historical-fake.ext2"
        self.image.write_bytes(b"fake image test only")
        self.sidecar = self.root / "historical-fake.ext2.sha256"
        self.sidecar.write_text(hashlib.sha256(self.image.read_bytes()).hexdigest()
                                + "  /artifacts/veraos-rootfs.ext2\n")
        self.legacy = {
            "schema": "VERA_OS_BOOT_CONTRACT_V1",
            "product": {
                "image_builder_version": "2024.02.4",
                "image_builder_commit": "a" * 40,
                "nixos_primary_path_status": "RETIRED",
            },
        }
        self.candidate = {
            "schema": "VERA_OS_RESIDENT_BUILDROOT_CANDIDATE_V1",
            "status": "SOURCE_CANDIDATE_NOT_BUILT",
            "candidate": {
                "version": "2025.02.18",
                "peeled_git_commit": "b" * 40,
            },
        }
        self.receipt = {
            "authority": False,
            "components": [
                {"file_count": count, "files": [
                    {"path": f"components/{name}/{i}.py",
                     "sha256": hashlib.sha256(str(i).encode()).hexdigest()}
                    for i in range(count)]}
                for name, count in (
                    ("vera_mono", 316), ("portal", 89),
                    ("pre_active", 18), ("volition", 8)
                )
            ],
        }

    def inspect(self):
        return inspect_image(self.legacy, self.candidate, self.receipt,
                             self.image, self.sidecar)

    def test_valid_historical_sha_does_not_promote_to_current_selection(self):
        outcome = self.inspect()
        self.assertEqual(outcome["status"], "HOLD_NOT_SELECTED")
        self.assertEqual(outcome["selected_stage_file_count"], 431)
        self.assertFalse(outcome["guest_qualified"])
        self.assertIn("HISTORICAL_AND_SELECTED_BUILDROOT_VERSIONS_DIFFER",
                      outcome["selection_hold_reasons"])

    def test_historical_image_byte_tamper_rejected(self):
        self.image.write_bytes(self.image.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ArtifactError, "do not match"):
            self.inspect()

    def test_forged_selected_stage_inventory_rejected(self):
        self.receipt["components"][0]["file_count"] += 1
        with self.assertRaisesRegex(ArtifactError, "file_count"):
            self.inspect()

    def test_wrong_image_sidecar_rejected(self):
        self.sidecar.write_text("0" * 64 + "  /artifacts/veraos-rootfs.ext2\n")
        with self.assertRaisesRegex(ArtifactError, "do not match"):
            self.inspect()

    def test_alignment_of_versions_is_still_not_runtime_qualification(self):
        self.legacy["product"]["image_builder_version"] = "2025.02.18"
        self.legacy["product"]["image_builder_commit"] = "b" * 40
        outcome = self.inspect()
        self.assertNotIn("HISTORICAL_AND_SELECTED_BUILDROOT_VERSIONS_DIFFER",
                         outcome["selection_hold_reasons"])
        self.assertIn("NO_GUEST_BOOT_OR_QUALIFIED_RUNTIME_CONSUMPTION",
                      outcome["selection_hold_reasons"])
        self.assertFalse(outcome["guest_qualified"])

    def test_missing_component_digest_rejected(self):
        self.receipt["components"][0]["files"][0]["sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ArtifactError, "missing exact SHA256"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
