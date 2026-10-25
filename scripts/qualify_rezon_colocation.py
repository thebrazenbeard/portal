"""Read-only, fail-closed preflight for a co-located PORTAL/Rezon observer.

This is a source/runtime probe, not a deployment, model qualification or grant.
It never prints the observer secret or an episode's synthesis content.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence

from portal.desktop_rezon import (
    RezonReadConfig,
    _valid_snapshot,
    authorized_rezon_route_ids,
    read_rezon_state,
)


def inspect_colocation(*, expected_episode: str, expected_port: int) -> dict[str, object]:
    """Inspect one explicit loopback target, without escalating privileges."""
    result: dict[str, object] = {
        "schema": "PORTAL_REZON_COLOCATION_PREFLIGHT_V1",
        "status": "HOLD",
        "reason": "observer_not_qualified",
        "source_client_authenticated": False,
        "deployed_route_verified": False,
        "protected_effect_authority": False,
    }
    config = RezonReadConfig.from_environment()
    if config is None:
        result["reason"] = "observer_configuration_missing_or_invalid"
        return result
    if config.episode_id != expected_episode or config.port != expected_port:
        result["reason"] = "expected_endpoint_mismatch"
        return result
    if f"rezon:{expected_episode}" not in authorized_rezon_route_ids():
        result["reason"] = "explicit_episode_admission_missing"
        return result

    try:
        # Keep the exact already-admitted port, episode, and secret together.
        # Discovery would independently re-read mutable environment variables.
        snapshot, fingerprint = read_rezon_state(config)
    except Exception:
        # Do not expose transport exception text; it might contain endpoint data.
        result["reason"] = "authenticated_observer_probe_failed"
        return result
    if (
        type(fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or type(snapshot) is not dict
        or not _valid_snapshot(snapshot, expected_episode)
    ):
        result["reason"] = "no_complete_authenticated_episode"
        return result

    result.update({
        "status": "PASS_READ_ONLY",
        "reason": "authenticated_current_synthesis_observed",
        "source_client_authenticated": True,
        "snapshot_sha256": fingerprint,
    })
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="exact existing episode ID")
    parser.add_argument("--port", required=True, type=int, help="expected co-located loopback port")
    args = parser.parse_args(argv)
    result = inspect_colocation(expected_episode=args.episode, expected_port=args.port)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS_READ_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
