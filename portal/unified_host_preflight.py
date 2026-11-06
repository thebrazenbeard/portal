"""Fail-closed *observational* preflight for a multi-host Vera pilot.

Consumes an operator-produced, non-secret host observation receipt. It makes no
network connections, starts no services, and confers no runtime/effect authority.
Even a PASS means only that a separate, live runtime qualification may begin.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA = "VERA_ECOSYSTEM_HOST_PREFLIGHT_V1"
MAX_EVIDENCE_BYTES = 32 * 1024
REQUIRED_PROBES: dict[str, str] = {
    "control.vera_mono_pilot": "WINDOWS_SERVICE",
    "control.veramesh_tunnel": "WINDOWS_SERVICE",
    "control.ollama_tags": "HTTP",
    "worker_a.ollama_tags": "HTTP",
    "worker_b.rezon_healthz": "HTTP",
    "worker_b.rezon_readyz": "AUTHENTICATED_HTTP",
    "worker_b.llama_models": "HTTP",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STRICT_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z\Z")
_ALLOWED_TOP = frozenset({"schema", "observed_at_utc", "observations", "model_inventory", "model_selection"})
_ALLOWED_OBSERVATION = frozenset({"id", "kind", "status", "credential_applied"})
_ALLOWED_INVENTORY = frozenset({"host", "model_tag", "digest_kind", "digest_sha256"})
_ALLOWED_MODEL = _ALLOWED_INVENTORY | {"incremental_paid_compute"}
_DIGEST_KINDS = ("OLLAMA_TAG_DIGEST", "GGUF_FILE_SHA256")
_HOST_ROLES = ("control", "worker_a", "worker_b")


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or _STRICT_UTC.fullmatch(value) is None:
        raise ValueError(f"{field} must be an RFC3339 UTC Z timestamp")
    try:
        date = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
    if date.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC")
    return date


def _exact_keys(value: object, keys: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field} has missing or unexpected fields")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous receipts before dict conversion loses duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("evidence contains duplicate JSON object keys")
        result[key] = value
    return result


def evaluate_preflight(
    evidence: object, *, now: datetime | None = None, max_age_seconds: int = 120,
) -> dict[str, object]:
    """Validate evidence shape and classify prerequisites; never authorize startup.

    A process/HTTP result is a *reported observation*, not attestation. The
    independent consumer must still verify provenance and the live installation.
    """
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise ValueError("max_age_seconds must be an integer from 1 to 3600")
    root = _exact_keys(evidence, _ALLOWED_TOP, "preflight evidence")
    if root["schema"] != SCHEMA:
        raise ValueError("preflight evidence schema mismatch")
    captured = _utc(root["observed_at_utc"], field="observed_at_utc")
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")

    reasons: list[str] = []
    if captured > current + timedelta(seconds=5):
        reasons.append("observation timestamp is in the future")
    if current - captured > timedelta(seconds=max_age_seconds):
        reasons.append("host observations have expired")

    raw_observations = root["observations"]
    if not isinstance(raw_observations, list):
        raise ValueError("observations must be an array")
    observed: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_observations):
        probe = _exact_keys(raw, _ALLOWED_OBSERVATION, f"observations[{index}]")
        probe_id = probe["id"]
        if not isinstance(probe_id, str) or probe_id not in REQUIRED_PROBES:
            raise ValueError(f"observations[{index}] has an unknown probe id")
        if probe_id in observed:
            raise ValueError(f"duplicate observation: {probe_id}")
        if probe["kind"] != REQUIRED_PROBES[probe_id]:
            raise ValueError(f"{probe_id} has an incorrect probe kind")
        if type(probe["credential_applied"]) is not bool:
            raise ValueError(f"{probe_id} credential_applied must be boolean")
        if probe["kind"] == "WINDOWS_SERVICE":
            if probe["status"] not in ("RUNNING", "STOPPED", "UNKNOWN"):
                raise ValueError(f"{probe_id} service status invalid")
            if probe["credential_applied"] is not False:
                raise ValueError(f"{probe_id} service probe cannot apply HTTP credentials")
        else:
            if type(probe["status"]) is not int or not 100 <= probe["status"] <= 599:
                raise ValueError(f"{probe_id} HTTP status invalid")
            if probe["kind"] == "HTTP" and probe["credential_applied"] is not False:
                raise ValueError(f"{probe_id} public observation cannot claim credentials")
        observed[probe_id] = probe

    for probe_id, expected_kind in REQUIRED_PROBES.items():
        row = observed.get(probe_id)
        if row is None:
            reasons.append(f"missing probe: {probe_id}")
        elif expected_kind == "WINDOWS_SERVICE":
            if row["status"] != "RUNNING":
                reasons.append(f"{probe_id} is {row['status']}")
        elif expected_kind == "AUTHENTICATED_HTTP":
            if not row["credential_applied"]:
                reasons.append(f"{probe_id} lacks an authenticated probe")
            elif row["status"] != 200:
                reasons.append(f"{probe_id} authenticated readiness returned {row['status']}")
        elif row["status"] != 200:
            reasons.append(f"{probe_id} returned HTTP {row['status']}")

    raw_inventory = root["model_inventory"]
    if not isinstance(raw_inventory, list) or len(raw_inventory) > 64:
        raise ValueError("model_inventory must be an array of at most 64 entries")
    inventory: set[tuple[str, str, str, str]] = set()
    model_names: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_inventory):
        entry = _exact_keys(raw, _ALLOWED_INVENTORY, f"model_inventory[{index}]")
        host, tag, kind, digest = (entry[field] for field in
                                   ("host", "model_tag", "digest_kind", "digest_sha256"))
        if (host not in _HOST_ROLES or not isinstance(tag, str) or not tag.strip()
                or kind not in _DIGEST_KINDS or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)):
            raise ValueError(f"model_inventory[{index}] has an invalid model identity")
        identity = (host, tag, kind, digest)
        name = (host, tag, kind)
        if name in model_names:
            raise ValueError(f"ambiguous duplicate model_inventory tag at {index}")
        model_names.add(name)
        inventory.add(identity)

    model = _exact_keys(root["model_selection"], _ALLOWED_MODEL, "model_selection")
    if model["host"] not in _HOST_ROLES:
        raise ValueError("model_selection host is invalid")
    if not isinstance(model["model_tag"], str) or not model["model_tag"].strip():
        reasons.append("selected local model tag not evidenced")
    if not isinstance(model["digest_sha256"], str) or not _SHA256.fullmatch(model["digest_sha256"]):
        reasons.append("selected local model digest not evidenced")
    if model["digest_kind"] not in _DIGEST_KINDS:
        reasons.append("selected model digest kind not evidenced")
    selected_identity = tuple(model[key] for key in ("host", "model_tag", "digest_kind", "digest_sha256"))
    if not all(isinstance(part, str) for part in selected_identity) or selected_identity not in inventory:
        reasons.append("selected model identity absent from same-receipt host inventory")
    if model["incremental_paid_compute"] is not False:
        reasons.append("selected inference route does not prove zero incremental paid compute")

    return {
        "schema": "VERA_ECOSYSTEM_HOST_PREFLIGHT_VERDICT_V1",
        "status": "HOLD" if reasons else "OBSERVATIONAL_PREFLIGHT_PASS",
        "reason_codes": reasons,
        "probe_count": len(observed),
        "next_gate": "LIVE_INSTALL_ROUTE_RUNTIME_CONSUMPTION_AND_EFFECT_QUALIFICATION",
        "deployment_authorized": False,
        "effect_authority_granted": False,
        "evidence_class": "OPERATOR_REPORTED_OBSERVATION_NOT_INDEPENDENT_ATTESTATION",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        with args.evidence.open("rb") as source:
            payload = source.read(MAX_EVIDENCE_BYTES + 1)
        if len(payload) > MAX_EVIDENCE_BYTES:
            raise ValueError("evidence exceeds 32 KiB limit")
        evidence = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys,
        )
        verdict = evaluate_preflight(evidence, max_age_seconds=args.max_age_seconds)
    except (ValueError, OSError, UnicodeDecodeError, RecursionError) as exc:
        print(json.dumps({"status": "INVALID_EVIDENCE", "error": str(exc)}))
        return 3
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["status"] == "OBSERVATIONAL_PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
