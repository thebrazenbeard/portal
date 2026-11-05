"""Recoverable *observations* for the read-only desktop file bridge.

This adapter never grants an effect or authenticates a producer. In particular,
files writable by the same Windows user as the bridge are not host receipts.
An independently authenticated host receipt is a separate, unimplemented gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable


_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_SCHEMA = "PORTAL_READ_ONLY_BRIDGE_INTENT_V1"
_MAX_RESPONSE = 1024 * 1024


class BridgeIntentConflict(ValueError):
    """An operation ID is already bound to a different or invalid intent."""


@dataclass(frozen=True)
class ReadOnlyBridgeObservation:
    request_id: str
    state: str
    result: dict[str, object] | None = None
    producer_authenticated: bool = False
    protected_effect_authority: bool = False


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _exclusive_json(
    path: Path,
    data: object,
    *,
    check_path: Callable[[Path], None],
) -> None:
    """Publish complete bytes with no-overwrite on normal process restarts.

    A file fsync does not prove directory-entry durability across power loss.
    The caller's rooted-path checks also cannot eliminate an active same-user
    TOCTOU reparse attack on Windows; such runtime paths are not a trust root.
    """
    check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    check_path(path)
    fd, temporary = tempfile.mkstemp(
        prefix=".readonly-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json_bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        check_path(path)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path, *, limit: int) -> object:
    if path.is_symlink():
        raise ValueError("symlink file refused")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("file exceeds bound")
    return json.loads(payload.decode("utf-8-sig"))


class ReadOnlyBridgeClient:
    """Submit once, later observe by the same ID; never replay an unknown call.

    Only ``desktop_status`` is admitted. File response observations are always
    ``UNVERIFIED_TRANSPORT`` evidence, even if request IDs and content match.
    They cannot be promoted to a host-issued, authenticated effect receipt.
    """

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).absolute()
        self.bridge = self.runtime_root / "bridge"
        self._check_rooted(self.bridge / "requests" / "probe.json")
        bridge = self.bridge
        self.intents = bridge / "readonly_intents"
        self.requests = bridge / "requests"
        self.claimed = bridge / "claimed"
        self.responses = bridge / "responses"

    def _check_rooted(self, path: Path) -> None:
        """Reject existing symlink/junction ancestors and lexical escapes.

        This is a defensive admission check, not a same-user race-proof ACL
        boundary. The installed bridge requires independent host ownership.
        """
        try:
            relative = path.relative_to(self.runtime_root)
        except ValueError as exc:
            raise BridgeIntentConflict("bridge path escapes runtime root") from exc
        cursor = self.runtime_root
        for part in ("", *relative.parts[:-1]):
            if part:
                cursor = cursor / part
            junction = getattr(cursor, "is_junction", None)
            if cursor.is_symlink() or (junction is not None and junction()):
                raise BridgeIntentConflict("bridge ancestor is a symlink/junction")
        expected = self.runtime_root.resolve() / relative.parent
        if path.parent.resolve(strict=False) != expected:
            raise BridgeIntentConflict("bridge path resolves outside expected root")

    @staticmethod
    def _id(request_id: str) -> str:
        if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
            raise ValueError("invalid request ID")
        return request_id

    @classmethod
    def _intent(cls, request_id: str, command: str) -> dict[str, str]:
        if command != "desktop_status":
            raise PermissionError("only desktop_status is read-only admitted")
        body = {"command": command, "request_id": cls._id(request_id)}
        return {
            "schema": _SCHEMA,
            "command": command,
            "request_id": request_id,
            "request_sha256": hashlib.sha256(_json_bytes(body)).hexdigest(),
        }

    def submit_once(
        self, request_id: str, *, command: str = "desktop_status"
    ) -> None:
        """Reserve the ID before enqueueing, without a blind retry path.

        If submission is interrupted after reserving its ID, the outcome is
        unknown. Repeated calls never create a second request file after a
        normal process restart. Power-loss durability is unverified.
        """
        intent = self._intent(request_id, command)
        intent_path = self.intents / f"{request_id}.json"
        self._check_rooted(intent_path)
        for directory in (self.requests, self.claimed, self.responses):
            self._check_rooted(directory / f"{request_id}.json")
        if intent_path.exists() or intent_path.is_symlink():
            self._verify_intent(request_id, command)
            return
        if any(
            (directory / f"{request_id}.json").exists()
            for directory in (self.requests, self.claimed, self.responses)
        ):
            raise BridgeIntentConflict("bridge ID was already in use")
        try:
            _exclusive_json(intent_path, intent, check_path=self._check_rooted)
        except FileExistsError:
            self._verify_intent(request_id, command)
            return
        try:
            _exclusive_json(
                self.requests / f"{request_id}.json",
                {"command": command, "request_id": request_id},
                check_path=self._check_rooted,
            )
        except FileExistsError as exc:
            raise BridgeIntentConflict(
                "bridge request path raced with this intent"
            ) from exc

    def _verify_intent(self, request_id: str, command: str) -> None:
        self._check_rooted(self.intents / f"{request_id}.json")
        try:
            observed = _read_json(self.intents / f"{request_id}.json", limit=4096)
        except (OSError, ValueError) as exc:
            raise BridgeIntentConflict("stored intent is unreadable") from exc
        if observed != self._intent(request_id, command):
            raise BridgeIntentConflict("operation ID already names a conflicting intent")

    def observe(self, request_id: str) -> ReadOnlyBridgeObservation:
        request_id = self._id(request_id)
        self._verify_intent(request_id, "desktop_status")
        request = self.requests / f"{request_id}.json"
        claimed = self.claimed / f"{request_id}.json"
        response = self.responses / f"{request_id}.json"
        for path in (request, claimed, response):
            self._check_rooted(path)
        if response.exists() or response.is_symlink():
            # This rejects the known prewritten-before-claim case. A same-user
            # process can still forge all three files: no authentication claim.
            if request.exists() and not claimed.exists():
                return ReadOnlyBridgeObservation(request_id, "UNATTRIBUTED_RESPONSE")
            try:
                data = _read_json(response, limit=_MAX_RESPONSE)
            except (OSError, ValueError):
                return ReadOnlyBridgeObservation(
                    request_id, "INVALID_RESPONSE_PRESERVED"
                )
            if (
                not isinstance(data, dict)
                or data.get("schema") != "VERA_UNIFIED_BRIDGE_RESPONSE_V1"
                or data.get("request_id") != request_id
                or not isinstance(data.get("runtime_id"), str)
                or not data.get("runtime_id")
                or type(data.get("ok")) is not bool
            ):
                return ReadOnlyBridgeObservation(
                    request_id, "INVALID_RESPONSE_PRESERVED"
                )
            if data.get("ok") is not True:
                return ReadOnlyBridgeObservation(
                    request_id, "OBSERVED_FAILURE_UNVERIFIED_TRANSPORT"
                )
            result = data.get("result")
            if (
                not isinstance(result, dict)
                or result.get("protected_effect_authority") is not False
            ):
                return ReadOnlyBridgeObservation(
                    request_id, "INVALID_RESPONSE_PRESERVED"
                )
            return ReadOnlyBridgeObservation(
                request_id,
                "OBSERVED_SUCCESS_UNVERIFIED_TRANSPORT",
                result=result,
            )
        if claimed.exists() or (not request.exists()):
            return ReadOnlyBridgeObservation(request_id, "OUTCOME_UNKNOWN")
        return ReadOnlyBridgeObservation(request_id, "PENDING_UNCLAIMED")

    def wait(
        self,
        request_id: str,
        *,
        timeout_seconds: float = 2.0,
        poll_seconds: float = 0.01,
    ) -> ReadOnlyBridgeObservation:
        if timeout_seconds < 0 or poll_seconds <= 0:
            raise ValueError("invalid polling interval")
        deadline = time.monotonic() + timeout_seconds
        while True:
            observation = self.observe(request_id)
            if observation.state not in {"PENDING_UNCLAIMED", "OUTCOME_UNKNOWN"}:
                return observation
            if time.monotonic() >= deadline:
                return observation
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
