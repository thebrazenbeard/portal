"""Source-only verifier for an explicitly pinned, read-only metadata envelope.

No Executor connection, execution driver, authority grant or credential discovery.
HMAC verifies possession of an owner-configured issuer key, not a workstation
or remote host identity. The owner must protect the trust context and the one
configured replay database, and separately verify Git/Executor currentness.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
from types import MappingProxyType
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
_MAC_DOMAIN = b"PORTAL_EXECUTOR_READONLY_ENVELOPE_V1\x00"


_RESERVATION_DDL = (
    "CREATE TABLE IF NOT EXISTS readonly_envelope_reservations ("
    "issuer_id TEXT NOT NULL, nonce TEXT NOT NULL,"
    "dispatch_id TEXT NOT NULL,"
    "attempt_id TEXT NOT NULL,"
    "payload_sha256 TEXT NOT NULL,"
    "PRIMARY KEY (issuer_id, nonce),"
    "UNIQUE (issuer_id, dispatch_id),"
    "UNIQUE (issuer_id, attempt_id))"
)


class ReservationStorageError(RuntimeError):
    """Reservation state unavailable or malformed; never means replay."""


@dataclass(frozen=True)
class ReadonlyEnvelopeTrust:
    """An owner-provisioned configuration, not authenticated identity proof.

    The caller creating this object must independently own its issuer registry
    and reservation database. Request data must NEVER construct this context.
    """
    issuer_keys: Mapping[str, bytes]
    nonce_db: Path

    def __post_init__(self) -> None:
        if not isinstance(self.issuer_keys, Mapping) or not self.issuer_keys:
            raise ValueError("trusted issuer registry is required")
        keys = dict(self.issuer_keys)
        if any(
            type(issuer) is not str or _IDENT.fullmatch(issuer) is None
            or type(key) is not bytes or len(key) < 32
            for issuer, key in keys.items()
        ):
            raise ValueError("invalid trusted issuer registry")
        if not isinstance(self.nonce_db, (Path, str)):
            raise ValueError("reservation store path is required")
        destination = Path(self.nonce_db)
        if not destination.is_absolute():
            raise ValueError("reservation store path must be absolute")
        if len(set(keys.values())) != len(keys):
            raise ValueError("duplicate issuer keys are forbidden")
        object.__setattr__(self, "issuer_keys", MappingProxyType(keys))
        object.__setattr__(self, "nonce_db", destination)


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

    def __bool__(self) -> bool:
        raise TypeError("reservation evidence is not execution authorization")


@dataclass(frozen=True)
class ConsumedReservationObservation:
    """Terminal row observation, never authorization or proof of remote work."""

    status: str = field(
        default="CONSUMED_EXACT_DO_NOT_REDISPATCH", init=False
    )
    execution_authorized: bool = field(default=False, init=False)
    host_authenticated: bool = field(default=False, init=False)

    def __bool__(self) -> bool:
        raise TypeError("reconciliation observation is not execution authorization")


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
    trust: ReadonlyEnvelopeTrust,
    expected: Mapping[str, str],
) -> ReservedReadonlyEnvelope:
    """Validate a MAC under owner-configured trust, reserving local replay IDs.

    No remote host authentication, live ref verification or execution authority.
    A consumer must independently provide those controls.
    """
    return _verify_and_reserve_with_clock(
        payload, signature_hex, trust=trust, expected=expected,
        clock=lambda: int(time.time()),
    )


def _check_reservation_schema(
    conn: sqlite3.Connection, *, create_if_missing: bool
) -> None:
    """Check current schema and affected indexes, not filesystem custody."""
    ddl = _RESERVATION_DDL
    existing = conn.execute(
        "SELECT type, sql FROM sqlite_master WHERE name = ?",
        ("readonly_envelope_reservations",),
    ).fetchone()
    if existing is not None:
        normalize = lambda value: "".join(value.lower().split()).replace(
            "ifnotexists", ""
        )
        if existing[0] != "table" or normalize(existing[1] or "") != normalize(ddl):
            raise ReservationStorageError("reservation database schema integrity mismatch")
    if existing is None and not create_if_missing:
        raise ReservationStorageError("reservation table missing")
    if create_if_missing:
        conn.execute(ddl)
    trigger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? LIMIT 1",
        ("readonly_envelope_reservations",),
    ).fetchone()
    if trigger is not None:
        raise ReservationStorageError("reservation database trigger integrity mismatch")
    indexes = conn.execute(
        "PRAGMA index_list(readonly_envelope_reservations)"
    ).fetchall()
    specs = set()
    for _, index_name, unique, origin, partial in indexes:
        quoted = '"' + index_name.replace('"', '""') + '"'
        columns = tuple(row[2] for row in conn.execute(
            f"PRAGMA index_info({quoted})"
        ))
        if unique != 1 or partial != 0:
            raise ReservationStorageError("reservation index integrity mismatch")
        specs.add((origin, columns))
    required = {
        ("pk", ("issuer_id", "nonce")),
        ("u", ("issuer_id", "dispatch_id")),
        ("u", ("issuer_id", "attempt_id")),
    }
    if len(indexes) != 3 or specs != required:
        raise ReservationStorageError("reservation index integrity mismatch")


def _authenticate_metadata(
    payload: bytes, signature_hex: str, *,
    trust: ReadonlyEnvelopeTrust, expected: Mapping[str, str],
    clock: Callable[[], int] | None,
) -> tuple[dict, int, int]:
    """MAC and exact scope, with optional current-time gate for admission."""
    if not isinstance(payload, bytes) or len(payload) > _MAX_PAYLOAD:
        raise ValueError("invalid envelope size or bytes")
    if not isinstance(trust, ReadonlyEnvelopeTrust):
        raise ValueError("owner-provisioned trust configuration is required")
    if type(signature_hex) is not str or _MAC.fullmatch(signature_hex) is None:
        raise ValueError("invalid envelope signature")
    if clock is not None and not callable(clock):
        raise ValueError("invalid verifier clock")
    def read_clock() -> int:
        observed = clock()
        if type(observed) is not int or observed < 0:
            raise ValueError("invalid verifier clock")
        return observed
    if not isinstance(expected, Mapping):
        raise ValueError("exact expected scope is required")
    expected = dict(expected)
    if set(expected) != _PINNED or any(type(v) is not str for v in expected.values()):
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
    issuer = raw.get("issuer_id")
    if type(issuer) is not str or issuer not in trust.issuer_keys:
        raise ValueError("unknown envelope issuer")
    issuer_key = trust.issuer_keys[issuer]
    calculated = hmac.new(
        issuer_key, _MAC_DOMAIN + payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calculated, signature_hex):
        raise ValueError("envelope signature mismatch")
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
        or (clock is not None and not (issued <= read_clock() < expiry))
    ):
        raise ValueError("envelope expired, premature or unbounded")

    return raw, issued, expiry


def _verify_and_reserve_with_clock(
    payload: bytes,
    signature_hex: str,
    *,
    trust: ReadonlyEnvelopeTrust,
    expected: Mapping[str, str],
    clock: Callable[[], int],
) -> ReservedReadonlyEnvelope:
    """Internal testable algorithm. Production calls use system wall time."""
    raw, issued, expiry = _authenticate_metadata(
        payload, signature_hex, trust=trust, expected=expected, clock=clock
    )
    read_clock = clock

    # Storage owner must protect the database file, parent directory, and
    # backups. This verification cannot defeat an OS-level DB replacement.
    destination = trust.nonce_db
    if (not destination.is_absolute() or not destination.parent.is_dir()
            or destination.is_symlink()):
        raise ValueError("untrusted reservation database path")
    ddl = _RESERVATION_DDL
    try:
        with closing(sqlite3.connect(destination, timeout=10)) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                _check_reservation_schema(conn, create_if_missing=True)
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
                if not (issued <= read_clock() < expiry):
                    raise ValueError("envelope expired during reservation")
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValueError("envelope replay detected") from exc
    except sqlite3.DatabaseError as exc:
        raise ReservationStorageError("reservation storage unavailable or corrupt") from exc
    return ReservedReadonlyEnvelope(
        issuer_id=raw["issuer_id"],
        repository=raw["repository"],
        ref=raw["ref"],
        exact_head=raw["exact_head"],
        target_id=raw["target_id"],
        dispatch_id=raw["dispatch_id"],
        attempt_id=raw["attempt_id"],
    )


def reconcile_consumed_readonly_envelope(
    payload: bytes, signature_hex: str, *,
    trust: ReadonlyEnvelopeTrust,
    expected: Mapping[str, str],
) -> ConsumedReservationObservation:
    """Observe a previously consumed admission; NEVER authorize redispatch.

    A matching row was visible in the SQLite snapshot opened via configured
    path. Missing state is indeterminate; no absence or retry grant is returned.
    Schema checks cannot establish database continuity, identity or custody.
    """
    raw, _, _ = _authenticate_metadata(
        payload, signature_hex, trust=trust, expected=expected, clock=None
    )
    destination = trust.nonce_db
    if (not destination.is_absolute() or not destination.exists()
            or not destination.is_file() or destination.is_symlink()
            or not destination.parent.is_dir()):
        raise ReservationStorageError("reservation storage missing or untrusted")
    try:
        # URI read-only mode prevents implicit DB creation on missing files.
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro",
                                     uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            _check_reservation_schema(conn, create_if_missing=False)
            rows = conn.execute(
                "SELECT nonce,dispatch_id,attempt_id,payload_sha256 "
                "FROM readonly_envelope_reservations WHERE issuer_id=? AND "
                "(nonce=? OR dispatch_id=? OR attempt_id=?)",
                (raw["issuer_id"],raw["nonce"],raw["dispatch_id"],raw["attempt_id"]),
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ReservationStorageError("reservation storage unavailable or corrupt") from exc
    if not rows:
        raise ReservationStorageError(
            "reservation not observed in this snapshot; retry forbidden"
        )
    expected_row = (
        raw["nonce"], raw["dispatch_id"], raw["attempt_id"],
        hashlib.sha256(payload).hexdigest(),
    )
    if len(rows) != 1 or tuple(rows[0]) != expected_row:
        raise ReservationStorageError("reservation identifier or payload conflict")
    return ConsumedReservationObservation()
