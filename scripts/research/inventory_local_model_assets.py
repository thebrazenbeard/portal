"""Bounded read-only inventory of local model *assets*, not usable models.

No network, model parsing, hashing of large weights, runtime invocation, or
external write. Root and descendant symlinks are not followed. File metadata
can be stale or forged; presence is never promoted to runtime readiness.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

WEIGHT_SUFFIXES = {".gguf", ".safetensors"}
SHARD_RE = re.compile(r"^(?:model(?:\.safetensors)?[-.])(\d{5})-of-(\d{5})\.safetensors$")
MAX_OUTPUT_FILES = 512


def classify(name: str) -> str | None:
    lower = name.lower()
    if lower.startswith(("mmproj", "projector")) and lower.endswith(".gguf"):
        return "MULTIMODAL_PROJECTOR"
    if lower == "adapter_model.safetensors" or lower.startswith("adapter_model."):
        return "ADAPTER_WEIGHTS"
    if SHARD_RE.fullmatch(lower):
        return "WEIGHT_SHARD"
    if lower.endswith(".gguf"):
        return "GGUF_WEIGHT_FILE"
    if lower.endswith(".safetensors"):
        return "SAFETENSORS_WEIGHT_FILE"
    return None


def inventory(roots, *, max_entries: int = 12000, max_depth: int = 12) -> dict:
    if type(max_entries) is not int or not 1 <= max_entries <= 100000:
        raise ValueError("invalid entry bound")
    if type(max_depth) is not int or not 0 <= max_depth <= 30:
        raise ValueError("invalid depth bound")
    if not isinstance(roots, (list, tuple)) or not 1 <= len(roots) <= 8:
        raise ValueError("1..8 explicit roots required")
    result = {"claim_limit": "FILESYSTEM_METADATA_ONLY",
              "model_execution_verified": False,
              "model_loadability_verified": False,
              "content_integrity_verified": False,
              "runtime_consumption_verified": False,
              "truncated": False, "roots": len(roots), "files": [], "shard_groups": []}
    scanned = 0
    grouped = {}
    for root_i, root_arg in enumerate(roots):
        base = Path(root_arg)
        if base.is_symlink() or not base.is_dir():
            raise ValueError("root missing, symlinked or not a directory")
        for directory, directories, filenames in os.walk(base, followlinks=False):
            path = Path(directory)
            depth = len(path.relative_to(base).parts)
            if depth >= max_depth:
                directories[:] = []
            else:
                directories[:] = sorted(
                    n for n in directories if not (path / n).is_symlink()
                )
            for name in sorted(filenames):
                scanned += 1
                if scanned > max_entries:
                    result["truncated"] = True
                    return result
                kind = classify(name)
                if kind is None:
                    continue
                file = path / name
                if file.is_symlink() or not file.is_file():
                    continue
                if len(result["files"]) >= MAX_OUTPUT_FILES:
                    result["truncated"] = True
                    return result
                try:
                    size = file.stat().st_size
                except OSError:
                    continue
                if size <= 0:
                    continue
                relative = file.relative_to(base).as_posix()
                result["files"].append({"root_index": root_i,
                                        "relative_path": relative,
                                        "bytes": size,
                                        "kind": kind})
                if kind == "WEIGHT_SHARD":
                    match = SHARD_RE.fullmatch(name.lower())
                    assert match is not None
                    group_id = (root_i, str(file.parent.relative_to(base)).replace("\\", "/"),
                                int(match.group(2)))
                    grouped.setdefault(group_id, set()).add(int(match.group(1)))
    for (root_i, folder, expected), pieces in sorted(grouped.items()):
        result["shard_groups"].append({
            "root_index": root_i, "relative_dir": folder,
            "expected_file_count": expected, "observed_file_count": len(pieces),
            "filenames_complete_only": pieces == set(range(1, expected + 1)),
            "content_verified": False,
        })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--max-entries", type=int, default=12000)
    args = parser.parse_args(argv)
    result = inventory(args.root, max_entries=args.max_entries)
    print(json.dumps(result, sort_keys=True))
    return 2 if result["truncated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
