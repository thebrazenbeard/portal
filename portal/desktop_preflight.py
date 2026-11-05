"""Read-only staged-runtime evidence; never an installer or activation route.

This intentionally has no call to desktop_installer.install().  In that
module activate=False suppresses task registration but still starts a host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Callable

from .desktop_install import InstallSpec
from .runtime_source_attestation import verify_attestation


SCHEMA = "PORTAL_STAGED_RUNTIME_READ_ONLY_PREFLIGHT_V1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _redirected(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _path_is_direct(root: Path) -> bool:
    """Reject redirected ancestors as well as redirected release artifacts."""
    return root.is_absolute() and not any(
        _redirected(part) for part in (root, *root.parents)
    )


def _read_pinned(path: Path, expected_sha256: str) -> bytes:
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError("an external exact 64-character SHA-256 is required")
    if _redirected(path) or not path.is_file():
        raise RuntimeError(f"{path.name} missing or redirected")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise RuntimeError(f"{path.name} bytes do not match the external digest")
    return payload


def _layout_findings(root: Path) -> list[str]:
    """Inspect the exact existing staged tree without creating directories."""
    findings: list[str] = []
    for relative in (
        "sources", "state", "bridge", "bridge/requests", "bridge/responses",
        "host", "logs",
    ):
        path = root / relative
        if _redirected(path) or not path.is_dir():
            findings.append(f"STAGED_DIRECTORY_MISSING_OR_REDIRECTED:{relative}")
        elif os.name != "nt" and path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            findings.append(f"STAGED_DIRECTORY_BROADLY_WRITABLE:{relative}")
    return findings


def inspect_staged_runtime(
    runtime_root: Path,
    *,
    expected_install_id: str,
    expected_spec_sha256: str,
    expected_attestation_sha256: str,
    source_verifier: Callable[[Path, InstallSpec], dict[str, object]] = verify_attestation,
) -> dict[str, object]:
    """Inspect already staged bytes without starting/connecting to any service.

    The expected digests must come from a separately trusted release owner,
    not from files inside the runtime being inspected.  A passing result
    qualifies only the inspected bytes at this instant; it cannot prove
    process ownership, same-user isolation, a stable host, or effect authority.
    """
    root = Path(runtime_root)
    problems: list[str] = []
    source_bytes_verified = False
    permission_evidence = "NOT_EXAMINED"
    if not expected_install_id or not expected_install_id.strip():
        raise ValueError("expected_install_id is required")
    if not _SHA256.fullmatch(expected_spec_sha256):
        raise ValueError("expected_spec_sha256 must be a lowercase SHA-256")
    if not _SHA256.fullmatch(expected_attestation_sha256):
        raise ValueError("expected_attestation_sha256 must be a lowercase SHA-256")
    if not _path_is_direct(root) or not root.is_dir():
        problems.append("RUNTIME_ROOT_MISSING_OR_REDIRECTED")
    else:
        problems.extend(_layout_findings(root))
        # A DACL cannot isolate hostile processes running with the same user
        # token.  The Python POSIX mode approximation is insufficient on
        # Windows; a trusted release owner must separately inspect its ACL.
        if os.name == "nt":
            permission_evidence = "WINDOWS_DACL_OWNER_REVIEW_REQUIRED"
        else:
            mode = root.stat().st_mode
            if mode & (stat.S_IWGRP | stat.S_IWOTH):
                problems.append("RUNTIME_ROOT_GROUP_OR_WORLD_WRITABLE")
            permission_evidence = "POSIX_MODE_ONLY_SAME_USER_NOT_ISOLATED"

        try:
            _read_pinned(root / "RUNTIME_INSTALL_SPEC.json", expected_spec_sha256)
            spec = InstallSpec.read(root / "RUNTIME_INSTALL_SPEC.json")
            if spec.install_id != expected_install_id:
                raise RuntimeError("install_id does not match expected release")
            attestation = json.loads(
                _read_pinned(
                    root / "RUNTIME_SOURCE_ATTESTATION.json",
                    expected_attestation_sha256,
                ).decode("utf-8-sig")
            )
            if not isinstance(attestation, dict):
                raise RuntimeError("source attestation is not an object")
            observed = source_verifier(root, spec)
            if observed != attestation:
                raise RuntimeError("staged source verification differs from pinned attestation")
            result_path = root / "INSTALLATION_RESULT.json"
            if _redirected(result_path) or not result_path.is_file():
                raise RuntimeError("installation result is missing or redirected")
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if not isinstance(result, dict):
                raise RuntimeError("installation result is not an object")
            required_sources = {
                name: {"repository": source.repository, "ref": source.ref, "sha": source.sha}
                for name, source in spec.source_map().items()
            }
            if result.get("install_id") != spec.install_id or result.get("source_map") != required_sources:
                raise RuntimeError("installation result does not match pinned source spec")
            receipt = result.get("source_attestation")
            if not isinstance(receipt, dict) or receipt.get("sha256") != attestation.get("sha256"):
                raise RuntimeError("installation result attestation receipt mismatches")
            if result.get("protected_effect_authority") is not False:
                raise RuntimeError("unqualified protected effect authority flag")
            if result.get("native_openai_router_replaced") is not False:
                raise RuntimeError("unqualified native router replacement flag")
            source_bytes_verified = True
        except (
            OSError, ValueError, KeyError, TypeError, RuntimeError,
            UnicodeError, subprocess.SubprocessError,
        ):
            # The exception is evidence of a hold, not permission to repair
            # the staged directory or infer the installed process's behavior.
            problems.append("STAGED_SOURCE_OR_RECEIPT_UNVERIFIED")

    return {
        "schema": SCHEMA,
        "status": "SOURCE_PREFLIGHT_ONLY" if source_bytes_verified and not problems else "HOLD",
        "source_bytes_verified": source_bytes_verified,
        "permission_evidence": permission_evidence,
        "live_process_identity_verified": False,
        "scheduled_task_ownership_verified": False,
        "protected_effect_authority": False,
        "release_qualified": False,
        "host_started_by_preflight": False,
        "activation_attempted_by_preflight": False,
        "blockers": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only source check; never activation")
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--expected-install-id", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--expected-attestation-sha256", required=True)
    args = parser.parse_args()
    evidence = inspect_staged_runtime(
        args.runtime_root,
        expected_install_id=args.expected_install_id,
        expected_spec_sha256=args.expected_spec_sha256,
        expected_attestation_sha256=args.expected_attestation_sha256,
    )
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "SOURCE_PREFLIGHT_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
