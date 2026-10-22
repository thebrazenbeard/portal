import tempfile
import unittest
from pathlib import Path

from inventory_local_model_assets import classify, inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make(self, name, data=b"x"):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return file

    def test_gguf_file_not_proof_of_ready_model(self):
        self.make("models/foo.gguf")
        r = inventory([self.root])
        self.assertEqual(r["files"][0]["kind"], "GGUF_WEIGHT_FILE")
        self.assertFalse(r["model_loadability_verified"])
        self.assertFalse(r["runtime_consumption_verified"])

    def test_projector_not_independent_model(self):
        self.make("models/mmproj-fp16.gguf")
        self.assertEqual(inventory([self.root])["files"][0]["kind"],
                         "MULTIMODAL_PROJECTOR")

    def test_adapter_not_independent_model(self):
        self.make("adapters/adapter_model.safetensors")
        self.assertEqual(inventory([self.root])["files"][0]["kind"],
                         "ADAPTER_WEIGHTS")

    def test_completed_shards_filename_only(self):
        self.make("base/model-00001-of-00002.safetensors")
        self.make("base/model-00002-of-00002.safetensors")
        g = inventory([self.root])["shard_groups"][0]
        self.assertTrue(g["filenames_complete_only"])
        self.assertFalse(g["content_verified"])

    def test_missing_shard_filename_detection(self):
        self.make("base/model.safetensors-00002-of-00003.safetensors")
        self.assertFalse(inventory([self.root])["shard_groups"][0]["filenames_complete_only"])

    def test_symlinked_file_does_not_get_counted(self):
        self.make("target.gguf")
        try:
            (self.root / "alias.gguf").symlink_to(self.root / "target.gguf")
        except (OSError, NotImplementedError):
            self.skipTest("symlink requires permission")
        r = inventory([self.root])
        self.assertEqual(len(r["files"]), 1)

    def test_symlinked_root_denied(self):
        other = self.root.parent / (self.root.name + "_link")
        try:
            other.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink requires permission")
        self.addCleanup(lambda: other.unlink(missing_ok=True))
        with self.assertRaises(ValueError):
            inventory([other])

    def test_entry_cap_fail_closed(self):
        self.make("a.gguf")
        self.make("b.gguf")
        self.assertTrue(inventory([self.root], max_entries=1)["truncated"])

    def test_wrong_extension_not_counted(self):
        self.make("foo.gguf.txt")
        self.make("tokenizer.json")
        self.assertEqual(inventory([self.root])["files"], [])

    def test_no_roots_denied(self):
        with self.assertRaises(ValueError):
            inventory([])

    def test_hard_bound_invalid_values_denied(self):
        with self.assertRaises(ValueError):
            inventory([self.root], max_entries=True)
        with self.assertRaises(ValueError):
            inventory([self.root], max_depth=31)


if __name__ == "__main__":
    unittest.main()
