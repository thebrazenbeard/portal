"""Source-only verifier for an explicitly pinned, read-only metadata envelope.

No Executor connection, execution driver, authority grant or credential discovery.
HMAC verifies possession of a caller-supplied key ONLY; it does not authenticate
a workstation or authorize any effect. Live source-currentness must be checked
by a separate owning component. The reservation DB is caller-selected.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Mapping


_SCHEMA = "PORTAL_EXECUTOR_READONLY_ENVELOPE_V1"
_OPERATION = "READ_GITHUB_REFERENCE_METADATA"
_FIELDS = frozenset({
    "schema", "issuer_id", "operation", "effect_ceiling", "repository",
    "ref", "exact_head", "target_id", "dispatch_id", "attempt_id",
    "nonce", "not_before", "expires_at",
})
_PINNED = frozenset({
    "issuer_id", "repository", "ref", "exact_head", "target_id",
    "dispatch_id", "attempt_id",
})
_IDENT = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80}\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9_./-]{1,180}\Z")
_HEAD = re.compile(r"[0-9a-f]{40}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_MAC = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PAYLOAD = 4096
_MAX_TTL_SECONDS = 120


@dataclass(frozen=True)
class ReservedReadonlyEnvelope:
    """Verified against caller-pinned bytes; never an Executor effect grant."""

    issuer_id: str
    repository: str
    ref: str
    exact_head: str
    target_id: str
    dispatch_id: str
    attempt_id: str
    operation: str = _OPERATION
    effect_ceiling: str = "NO_EFFECT"
    execution_authorized: bool = field(default=False, init=False)
    host_authenticated: bool = field(default=False, init=False)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate envelope JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise ValueError("nonfinite JSON value")


def _required_match(value, expression, label):
    if type(value) is not str or expression.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")


def verify_and_reserve_readonly_envelope(
    payload: bytes,
    signature_hex: str,
    *,
    issuer_key: bytes,
    expected: Mapping[str, str],
    nonce_db: Path,
    now: int | None = None,
    clock: Callable[[], int] | None = None,
) -> ReservedReadonlyEnvelope:
    """Validate signed exact-scope metadata, atomically reserve it once.

    Caller must independently establish issuer key ownership, authorize scope,
    verify source currentness, and authenticate any eventual Executor transport.
    This function does not perform those operations or send a command.
    """
    if not isinstance(payload, bytes) or len(payload) > _MAX_PAYLOAD:
        raise ValueError("invalid envelope size or bytes")
    if not isinstance(issuer_key, bytes) or len(issuer_key) < 32:
        raise ValueError("issuer key must be caller-provisioned bytes")
    if type(signature_hex) is not str or _MAC.fullmatch(signature_hex) is None:
        raise ValueError("invalid envelope signature")
    if now is not None and clock is not None:
        raise ValueError("choose one verifier clock")
    if clock is not None and not callable(clock):
        raise ValueError("invalid verifier clock")
    def read_clock() -> int:
        observed = clock() if clock is not None else (
            now if now is not None else int(time.time())
        )
        if type(observed) is not int or observed < 0:
            raise ValueError("invalid verifier clock")
        return observed
    if not isinstance(expected, Mapping) or set(expected) != _PINNED:
        raise ValueError("exact expected scope is required")

    try:
        raw = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid envelope JSON") from exc
    if type(raw) is not dict or set(raw) != _FIELDS:
        raise ValueError("unexpected envelope fields")
    if raw["schema"] != _SCHEMA or raw["operation"] != _OPERATION:
        raise ValueError("unsupported metadata operation")
    if raw["effect_ceiling"] != "NO_EFFECT":
        raise ValueError("effect authorization forbidden")
    for field_name in ("issuer_id", "target_id", "dispatch_id", "attempt_id"):
        _required_match(raw[field_name], _IDENT, field_name)
    _required_match(raw["repository"], _REPO, "repository")
    if any(part in (".", "..") for part in raw["repository"].split("/")):
        raise ValueError("invalid repository component")
    _required_match(raw["ref"], _REF, "ref")
    ref_segments = raw["ref"][len("refs/heads/"):].split("/")
    if any(not seg or seg.startswith(".") or seg.endswith(".")
           or seg.endswith(".lock") or ".." in seg for seg in ref_segments):
        raise ValueError("invalid ref")
    _required_match(raw["exact_head"], _HEAD, "exact_head")
    _required_match(raw["nonce"], _NONCE, "nonce")
    if any(raw[key] != expected[key] for key in _PINNED):
        raise ValueError("envelope target not independently pinned")
    issued, expiry = raw["not_before"], raw["expires_at"]
    if (
        type(issued) is not int or type(expiry) is not int
        or issued < 0 or expiry <= issued
        or expiry - issued > _MAX_TTL_SECONDS
        or not (issued <= read_clock() < expiry)
    ):
        raise ValueError("envelope expired, premature or unbounded")

    calculated = hmac.new(issuer_key, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, signature_hex):
        raise ValueError("envelope signature mismatch")

    # Storage owner must protect the database file, parent directory, and
    # backups. This verification cannot defeat an OS-level DB replacement.
    destination = Path(nonce_db)
    if (not destination.is_absolute() or not destination.parent.is_dir()
            or destination.is_symlink()):
        raise ValueError("untrusted reservation database path")
    ddl = (
        "CREATE TABLE IF NOT EXISTS readonly_envelope_reservations ("
        "issuer_id TEXT NOT NULL, nonce TEXT NOT NULL,"
        "dispatch_id TEXT NOT NULL UNIQUE,"
        "attempt_id TEXT NOT NULL UNIQUE,"
        "payload_sha256 TEXT NOT NULL,"
        "PRIMARY KEY (issuer_id, nonce))"
    )
    with closing(sqlite3.connect(destination, timeout=10)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT type, sql FROM sqlite_master WHERE name = ?",
                ("readonly_envelope_reservations",),
            ).fetchone()
            if existing is not None:
                normalize = lambda value: "".join(value.lower().split()).replace(
                    "ifnotexists", ""
                )
                if existing[0] != "table" or normalize(existing[1] or "") != normalize(ddl):
                    raise ValueError("reservation database schema integrity mismatch")
            conn.execute(ddl)
            trigger = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? LIMIT 1",
                ("readonly_envelope_reservations",),
            ).fetchone()
            if trigger is not None:
                raise ValueError("reservation database trigger integrity mismatch")
            if not (issued <= read_clock() < expiry):
                raise ValueError("envelope expired during reservation")
            conn.execute(
                "INSERT INTO readonly_envelope_reservations"
                "(issuer_id, nonce, dispatch_id, attempt_id, payload_sha256)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    raw["issuer_id"], raw["nonce"], raw["dispatch_id"],
                    raw["attempt_id"], hashlib.sha256(payload).hexdigest(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError("envelope replay detected") from exc
    return ReservedReadonlyEnvelope(
        issuer_id=raw["issuer_id"],
        repository=raw["repository"],
        ref=raw["ref"],
        exact_head=raw["exact_head"],
        target_id=raw["target_id"],
        dispatch_id=raw["dispatch_id"],
        attempt_id=raw["attempt_id"],
    )
