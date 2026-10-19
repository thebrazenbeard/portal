#!/usr/bin/env python3
"""PORTAL source-only check: staged files vs fresh packed ext2 bytes."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

VERAOS_HEAD = "99584ceea2a22817b147fb802542efd1bb020e3f"
VERAOS_READER_BLOB = "60a914ba06d71709f38c23236c2c52b33724b017"
SCHEMA = "PORTAL_PACKED_EXT2_MEMBER_LOCK_V1"

class Hold(ValueError):
    pass

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, check=True)
    return p.stdout.strip()

def reader_for(repo: Path):
    if git(repo, "rev-parse", "HEAD") != VERAOS_HEAD:
        raise Hold("unselected VeraOS reader commit")
    source = repo / "tools/vm/Audit-VeraOSImage.py"
    if git(repo, "hash-object", str(source)) != VERAOS_READER_BLOB:
        raise Hold("VeraOS reader bytes disagree with pinned Git blob")
    spec = importlib.util.spec_from_file_location("veraos_packed_ext2_reader", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ExtRootfs

def staged_member(stage: Path, virtual: str) -> bytes:
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", virtual)
            or any(p in ("", ".", "..") for p in virtual[1:].split("/"))):
        raise Hold("invalid locked member path")
    member = stage
    for part in virtual[1:].split("/"):
        member = member / part
        if member.is_symlink():
            raise Hold("staged symlink must not count as file bytes")
    if not member.is_file() or not member.resolve().is_relative_to(stage):
        raise Hold("missing or escaping staged member")
    return member.read_bytes()

def verify(stage: Path, image: Path, lock: dict, veraos: Path) -> dict:
    if lock.get("schema") != SCHEMA or lock.get("kind") != "SYNTHETIC_DISPOSABLE_EXT2":
        raise Hold("unsupported lock kind (not real Buildroot evidence)")
    expected = lock.get("members")
    if (not isinstance(expected, dict) or not expected
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or re.fullmatch("[0-9a-f]{64}", v) is None
                   for k, v in expected.items())):
        raise Hold("invalid immutable member digest mapping")
    if (not isinstance(lock.get("image_sha256"), str)
            or re.fullmatch("[0-9a-f]{64}", lock["image_sha256"]) is None):
        raise Hold("missing packed-image digest")
    if stage.is_symlink() or not stage.is_dir() or image.is_symlink() or not image.is_file():
        raise Hold("stage/image must be real directory/regular file")
    stage = stage.resolve(strict=True)
    image = image.resolve(strict=True)
    reader_class = reader_for(veraos)
    packed_start = sha(image.read_bytes())
    stage_before = {p: sha(staged_member(stage, p)) for p in expected}
    reader = reader_class(image)
    packed_members = {p: reader.read(p) for p in expected}
    packed_after = sha(image.read_bytes())
    stage_after = {p: sha(staged_member(stage, p)) for p in expected}
    observed = {p: {"stage_sha256": stage_before[p],
                    "packed_sha256": sha(packed_members[p]) if packed_members[p] is not None else None,
                    "locked_sha256": expected[p]} for p in expected}
    issues = []
    if packed_start != packed_after:
        issues.append("packed image mutated during audit")
    if packed_after != lock["image_sha256"]:
        issues.append("packed image digest no longer matches audit lock")
    if stage_before != stage_after:
        issues.append("staged bytes mutated during audit")
    for p, record in observed.items():
        if (record["stage_sha256"] != record["locked_sha256"]
                or record["packed_sha256"] != record["locked_sha256"]):
            issues.append("stale or mismatched stage/packed member " + p)
    return {"status": "PASS_SYNTHETIC_ONLY" if not issues else "HOLD_STALE_ARTIFACT",
            "kind": "SYNTHETIC_DISPOSABLE_EXT2",
            "veraos_head": VERAOS_HEAD, "reader_blob": VERAOS_READER_BLOB,
            "image_sha256_before": packed_start, "image_sha256_after": packed_after,
            "members": observed, "issues": issues,
            "runtime_qualified": False, "guest_boot_verified": False}

def main() -> int:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--stage", type=Path, required=True)
    a.add_argument("--image", type=Path, required=True)
    a.add_argument("--lock", type=Path, required=True)
    a.add_argument("--veraos", type=Path, required=True)
    args = a.parse_args()
    try:
        result = verify(args.stage, args.image,
                        json.loads(args.lock.read_text(encoding="utf-8")), args.veraos)
    except (Hold, OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"status": "HOLD_UNVERIFIED", "issues": [str(exc)],
                  "runtime_qualified": False, "guest_boot_verified": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS_SYNTHETIC_ONLY" else 2

if __name__ == "__main__":
    sys.exit(main())

