"""Read-only real-image identity and selected-profile separation gate.

This gate never proves Buildroot reproduction, guest boot, runtime consumption,
later consumer custody, or protected-effect authority. HOLD is exit code 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


class ArtifactError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def embedded_sha256_sidecar(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ArtifactError("image sidecar unreadable") from exc
    rows = [line for line in text.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ArtifactError("image sidecar must contain exactly one digest")
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+.+)?", rows[0])
    if match is None:
        raise ArtifactError("image sidecar has malformed digest")
    return match.group(1).lower()


def selected_stage_inventory(receipt: dict) -> int:
    if receipt.get("authority") is not False:
        raise ArtifactError("selected stage receipt cannot assert build authority")
    components = receipt.get("components")
    if not isinstance(components, list) or len(components) != 4:
        raise ArtifactError("selected stage must include exactly four component rows")
    count = 0
    paths: set[str] = set()
    for component in components:
        entries = component.get("files")
        if not isinstance(entries, list) or len(entries) != component.get("file_count"):
            raise ArtifactError("component file_count differs from member list")
        for entry in entries:
            path = entry.get("path")
            digest = entry.get("sha256")
            if not isinstance(path, str) or not path.startswith("components/"):
                raise ArtifactError("invalid staged component path")
            if path in paths:
                raise ArtifactError("duplicate staged component path")
            if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
                raise ArtifactError("staged member is missing exact SHA256")
            paths.add(path)
            count += 1
    return count


def inspect_image(legacy: dict, candidate: dict, receipt: dict,
                  image: Path, sidecar: Path, expected_selected_files: int = 431) -> dict:
    if legacy.get("schema") != "VERA_OS_BOOT_CONTRACT_V1":
        raise ArtifactError("unexpected baseline contract schema")
    if candidate.get("schema") != "VERA_OS_RESIDENT_BUILDROOT_CANDIDATE_V1":
        raise ArtifactError("unexpected selected candidate schema")
    product = legacy.get("product")
    selected = candidate.get("candidate")
    if not isinstance(product, dict) or not isinstance(selected, dict):
        raise ArtifactError("missing Buildroot profile data")
    count = selected_stage_inventory(receipt)
    if count != expected_selected_files:
        raise ArtifactError("selected source receipt inventory does not match intended profile")
    expected_digest = embedded_sha256_sidecar(sidecar)
    observed_digest = sha256_file(image)
    if observed_digest != expected_digest:
        raise ArtifactError("physical image bytes do not match preserved sidecar SHA256")
    reasons = []
    if product.get("image_builder_version") != selected.get("version"):
        reasons.append("HISTORICAL_AND_SELECTED_BUILDROOT_VERSIONS_DIFFER")
    if product.get("image_builder_commit") != selected.get("peeled_git_commit"):
        reasons.append("HISTORICAL_AND_SELECTED_BUILDROOT_COMMITS_DIFFER")
    if product.get("nixos_primary_path_status") == "RETIRED":
        reasons.append("LEGACY_NIXOS_RETIREMENT_CANNOT_SET_CURRENT_PRIMARY_HOST")
    if candidate.get("status") == "SOURCE_CANDIDATE_NOT_BUILT":
        reasons.append("SELECTED_CANDIDATE_NOT_BUILT")
    reasons.extend((
        "SIDECAR_DIGEST_DOES_NOT_BIND_IMAGE_TO_SELECTED_STAGE",
        "NO_AUTHORITATIVE_CURRENT_OUTPUT_IMAGES_PRODUCER_RECEIPT",
        "NO_HELD_IMAGE_DESCRIPTOR_OR_BOOTLOADER_CONSUMER_BINDING",
        "NO_GUEST_BOOT_OR_QUALIFIED_RUNTIME_CONSUMPTION",
    ))
    return {
        "schema": "PORTAL_VERAOS_IMAGE_SELECTION_OBSERVATION_V1",
        "status": "HOLD_NOT_SELECTED",
        "physical_image_identity": "SHA256_MATCHES_SIDECAR",
        "image_bytes": image.stat().st_size,
        "image_sha256": observed_digest,
        "historical_buildroot_version": product.get("image_builder_version"),
        "historical_buildroot_commit": product.get("image_builder_commit"),
        "selected_candidate_version": selected.get("version"),
        "selected_candidate_commit": selected.get("peeled_git_commit"),
        "selected_stage_file_count": count,
        "candidate_status": candidate.get("status"),
        "guest_qualified": False,
        "protected_effect_authority": False,
        "selection_hold_reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-boot-contract", type=Path, required=True)
    parser.add_argument("--resident-candidate", type=Path, required=True)
    parser.add_argument("--selected-stage-receipt", type=Path, required=True)
    parser.add_argument("--real-historical-image", type=Path, required=True)
    parser.add_argument("--image-sha256-sidecar", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_image(
            json.loads(args.legacy_boot_contract.read_text(encoding="utf-8-sig")),
            json.loads(args.resident_candidate.read_text(encoding="utf-8-sig")),
            json.loads(args.selected_stage_receipt.read_text(encoding="utf-8-sig")),
            args.real_historical_image, args.image_sha256_sidecar,
        )
    except (ArtifactError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "REJECTED", "reason": str(exc)}, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())
