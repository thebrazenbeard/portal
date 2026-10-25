"""Optional, read-only Rezon Collective observer for PORTAL Desktop.

This adapter never creates an episode, submits a contribution, starts a model,
or obtains a worker/admin bearer. An operator must configure an exact existing
episode and a separate observer credential. Rezon output remains unverified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import http.client
import json
import os
import re
import secrets

from .desktop_http_deadline import _DeadlineHTTPConnection


_EPISODE_RE = re.compile(r"collective-[0-9a-f]{32}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_MAX_READ_BYTES = 128 * 1024
_PREFLIGHT_DOMAIN = b"rezon-state-read/preflight/v2\0"
_REQUEST_DOMAIN = b"rezon-state-read/request/v2\0"
_RESPONSE_DOMAIN = b"rezon-state-read/response/v2\0"


@dataclass(frozen=True)
class RezonReadConfig:
    episode_id: str
    port: int
    token: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> RezonReadConfig | None:
        episode_id = os.environ.get("PORTAL_REZON_EPISODE_ID", "")
        port_text = os.environ.get("PORTAL_REZON_PORT", "8765")
        token = os.environ.get("REZON_COLLECTIVE_STATE_READ_TOKEN", "")
        if (
            _EPISODE_RE.fullmatch(episode_id) is None
            or re.fullmatch(r"[1-9][0-9]{0,4}", port_text) is None
            or int(port_text) > 65535
            or _HEX_64.fullmatch(token) is None
            or len(set(token)) < 8
        ):
            return None
        return cls(episode_id=episode_id, port=int(port_text), token=token)


def authorized_rezon_route_ids() -> frozenset[str]:
    """Independent operator admission; discovery itself cannot grant access."""
    config = RezonReadConfig.from_environment()
    if config is None or os.environ.get("PORTAL_REZON_ADMIT_EPISODE_ID") != config.episode_id:
        return frozenset()
    return frozenset({f"rezon:{config.episode_id}"})


def _get_json(port: int, path: str, headers: dict[str, str]) -> tuple[dict, bytes, str | None]:
    """GET only, direct numeric loopback, bounded response and total deadline."""
    connection = _DeadlineHTTPConnection("127.0.0.1", port, timeout=3.0)
    response = None
    try:
        connection.request("GET", path, headers={**headers, "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Rezon observer HTTP status {response.status}")
        raw = response.read(_MAX_READ_BYTES + 1)
        if len(raw) > _MAX_READ_BYTES:
            raise RuntimeError("Rezon observer response exceeds limit")
        proof = response.getheader("X-Rezon-State-Proof")
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise RuntimeError("Rezon observer transport failed") from exc
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("Rezon observer sent malformed JSON") from exc
    if type(value) is not dict:
        raise RuntimeError("Rezon observer did not return an object")
    return value, raw, proof


def _valid_snapshot(snapshot: dict, episode_id: str) -> bool:
    if (
        snapshot.get("episode_id") != episode_id
        or snapshot.get("complete") is not True
        or snapshot.get("missing_agents") != []
        or type(snapshot.get("input_digest")) is not str
        or _HEX_64.fullmatch(snapshot["input_digest"]) is None
    ):
        return False
    syntheses = snapshot.get("syntheses")
    if type(syntheses) is not list:
        return False
    current = [item for item in syntheses if type(item) is dict and item.get("is_current") is True]
    if len(current) != 1:
        return False
    item = current[0]
    content = item.get("content")
    if type(content) is not str or not content.strip():
        return False
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeError:
        return False
    return (
        item.get("input_digest") == snapshot["input_digest"]
        and item.get("status") == "unverified_model_synthesis"
        and 0 < content_size <= 32 * 1024
        and type(item.get("synthesis_id")) is str
        and bool(item["synthesis_id"])
    )


def read_rezon_state(config: RezonReadConfig | None) -> tuple[dict, str]:
    """Authenticate both challenge and exact response bytes using Rezon V2 HMAC."""
    if config is None or _EPISODE_RE.fullmatch(config.episode_id) is None:
        raise ValueError("exact Rezon episode configuration is required")
    path = f"/v1/episodes/{config.episode_id}/state"
    client_nonce = secrets.token_hex(32)
    challenge, _, _ = _get_json(config.port, "/v1/read-challenge", {
        "X-Rezon-State-Path": path,
        "X-Rezon-Client-Nonce": client_nonce,
    })
    nonce = challenge.get("server_nonce")
    epoch = challenge.get("epoch")
    issued_ms = challenge.get("issued_ms")
    audience = f"127.0.0.1:{config.port}"
    if (
        type(nonce) is not str or _HEX_64.fullmatch(nonce) is None
        or type(epoch) is not str or _HEX_32.fullmatch(epoch) is None
        or type(issued_ms) is not int or not 0 < issued_ms < 2**64
        or challenge.get("expires_in_ms") != 10000
        or challenge.get("audience") != audience
        or challenge.get("state_path") != path
        or challenge.get("client_nonce") != client_nonce
        or type(challenge.get("proof")) is not str
        or _HEX_64.fullmatch(challenge["proof"]) is None
    ):
        raise RuntimeError("Rezon challenge authentication failed")
    context = (b"GET\0" + path.encode("ascii") + b"\0" + audience.encode("ascii")
               + b"\0" + bytes.fromhex(nonce) + bytes.fromhex(client_nonce)
               + issued_ms.to_bytes(8, "big") + bytes.fromhex(epoch))
    secret = bytes.fromhex(config.token)
    preflight = hmac.new(secret, _PREFLIGHT_DOMAIN + context, sha256).hexdigest()
    if not hmac.compare_digest(preflight, challenge["proof"]):
        raise RuntimeError("Rezon challenge authentication failed")
    request_proof = hmac.new(secret, _REQUEST_DOMAIN + context, sha256).hexdigest()
    authorization = f"Rezon-Read-HMAC {nonce}.{client_nonce}.{issued_ms}.{epoch}.{request_proof}"
    snapshot, raw, response_proof = _get_json(config.port, path, {
        "Authorization": authorization,
    })
    expected_response = hmac.new(
        secret, _RESPONSE_DOMAIN + context + sha256(raw).digest(), sha256,
    ).hexdigest()
    if (type(response_proof) is not str or _HEX_64.fullmatch(response_proof) is None
            or not hmac.compare_digest(expected_response, response_proof)):
        raise RuntimeError("Rezon response authentication failed")
    return snapshot, sha256(raw).hexdigest()


def discover_rezon_observer() -> tuple[str, str] | None:
    """Only advertise an already-complete, authentically observed episode."""
    config = RezonReadConfig.from_environment()
    if config is None or f"rezon:{config.episode_id}" not in authorized_rezon_route_ids():
        return None
    try:
        snapshot, fingerprint = read_rezon_state(config)
    except (RuntimeError, ValueError):
        return None
    if not _valid_snapshot(snapshot, config.episode_id):
        return None
    return config.episode_id, fingerprint


def invoke_rezon_observer(episode_id: str, observed_fingerprint: str | None,
                          task: str) -> str:
    """Return one current advisory; require exact request/episode/state binding."""
    config = RezonReadConfig.from_environment()
    if (config is None or episode_id != config.episode_id or task != episode_id
            or f"rezon:{episode_id}" not in authorized_rezon_route_ids()):
        raise ValueError("exact episode request/configuration mismatch")
    snapshot, fingerprint = read_rezon_state(config)
    if fingerprint != observed_fingerprint:
        raise RuntimeError("Rezon episode state changed after discovery")
    if not _valid_snapshot(snapshot, episode_id):
        raise RuntimeError("Rezon episode is incomplete or ambiguous")
    synthesis = next(item for item in snapshot["syntheses"] if item.get("is_current") is True)
    return json.dumps({
        "schema": "PORTAL_REZON_COLLECTIVE_ADVISORY_V1",
        "episode_id": episode_id,
        "input_digest": snapshot["input_digest"],
        "synthesis_id": synthesis["synthesis_id"],
        "synthesis_status": "unverified_model_synthesis",
        "content": synthesis["content"],
        "independent_hosts_verified": False,
        "protected_effect_authority": False,
    }, ensure_ascii=False, sort_keys=True)
