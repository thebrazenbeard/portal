"""Fail-closed client for an externally hosted monotonic authority.

This is a *client contract*, not an authority service. A signature proves the
configured signer responded; it does not prove durable storage, host custody,
exclusive ownership, or non-rollbackable state. Never provision the signing
key or trust configuration from a model request or an untrusted repository.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import re
import secrets
import socket
import struct
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_MAX_WIRE: Final = 4096
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REPLY_FIELDS: Final = frozenset({
    "schema", "decision", "authority_id", "host_id", "issuer_id",
    "witness_id", "request_digest", "nonce", "previous_epoch",
    "next_epoch", "receipt_sequence", "signature",
})


class AnchorUnavailable(RuntimeError):
    """The external host authority did not furnish a valid acknowledgment."""


def _token(value: str, field: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def _recv_exact(sock: socket.socket, needed: int) -> bytes:
    pieces: list[bytes] = []
    while needed:
        chunk = sock.recv(needed)
        if not chunk:
            raise AnchorUnavailable("truncated anchor reply")
        pieces.append(chunk)
        needed -= len(chunk)
    return b"".join(pieces)


@dataclass(frozen=True)
class PinnedAnchorEndpoint:
    """Deployment configuration; requires a *separately authenticated* loader."""

    socket_path: str
    signer_public_key: bytes
    authority_id: str
    host_id: str
    issuer_id: str
    witness_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.socket_path, str) or not self.socket_path.startswith("/"):
            raise ValueError("anchor socket path must be absolute")
        if len(self.socket_path.encode("utf-8")) > 100:
            raise ValueError("anchor socket path too long")
        if not isinstance(self.signer_public_key, bytes) or len(self.signer_public_key) != 32:
            raise ValueError("expected raw 32-byte Ed25519 public key")
        for name in ("authority_id", "host_id", "issuer_id", "witness_id"):
            _token(getattr(self, name), name)


class RemoteMonotonicAnchor:
    """Verifies signed compare-and-advance receipts, without local fallback.

    `advance` must be called before the local state transition it fences. A
    missing/invalid response is an UNKNOWN or DENY, not permission to retry
    an effect blindly. The service must perform atomic, durable CAS itself.
    """

    def __init__(self, endpoint: PinnedAnchorEndpoint, *, timeout: float = 0.5) -> None:
        if not isinstance(endpoint, PinnedAnchorEndpoint):
            raise TypeError("a pinned endpoint is required")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
            raise ValueError("timeout out of range")
        self._endpoint = endpoint
        self._timeout = float(timeout)
        self._key = Ed25519PublicKey.from_public_bytes(endpoint.signer_public_key)

    def advance(self, *, previous_epoch: int, request_digest: str) -> int:
        if isinstance(previous_epoch, bool) or not isinstance(previous_epoch, int) or previous_epoch < 0:
            raise ValueError("invalid previous_epoch")
        if previous_epoch >= 2**63 - 1:
            raise ValueError("epoch exhausted")
        if not isinstance(request_digest, str) or _SHA256.fullmatch(request_digest) is None:
            raise ValueError("request_digest must be lowercase SHA256 hex")
        e = self._endpoint
        request = {
            "schema": "PORTAL_MONOTONIC_CAS_V1",
            "authority_id": e.authority_id,
            "host_id": e.host_id,
            "issuer_id": e.issuer_id,
            "witness_id": e.witness_id,
            "request_digest": request_digest,
            "nonce": secrets.token_hex(24),
            "previous_epoch": previous_epoch,
            "next_epoch": previous_epoch + 1,
        }
        encoded = _canonical(request)
        if len(encoded) > _MAX_WIRE:
            raise AnchorUnavailable("oversized anchor request")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._timeout)
                conn.connect(e.socket_path)
                conn.sendall(struct.pack("!I", len(encoded)) + encoded)
                size = struct.unpack("!I", _recv_exact(conn, 4))[0]
                if size < 1 or size > _MAX_WIRE:
                    raise AnchorUnavailable("invalid anchor reply length")
                wire = _recv_exact(conn, size)
        except (OSError, TimeoutError) as exc:
            raise AnchorUnavailable("anchor transport unavailable") from None
        try:
            response = json.loads(wire.decode("utf-8"))
            if not isinstance(response, dict) or set(response) != _REPLY_FIELDS:
                raise ValueError("invalid receipt shape")
            if response["schema"] != "PORTAL_MONOTONIC_CAS_V1" or response["decision"] != "ADVANCED":
                raise ValueError("anchor refused advancement")
            if any(response[key] != request[key] for key in request):
                raise ValueError("anchor scope mismatch")
            if type(response["previous_epoch"]) is not int or type(response["next_epoch"]) is not int:
                raise ValueError("invalid epoch types")
            sequence = response["receipt_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise ValueError("invalid receipt sequence")
            sig = base64.b64decode(response.pop("signature"), validate=True)
            if len(sig) != 64:
                raise ValueError("invalid signature")
            self._key.verify(sig, _canonical(response))
        except (UnicodeError, TypeError, ValueError, KeyError, binascii.Error, InvalidSignature):
            raise AnchorUnavailable("anchor receipt did not verify") from None
        return response["next_epoch"]
