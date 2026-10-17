from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
import uuid

from .desktop_cognition import CognitionRoute
from .desktop_runtime import ResidentCognitionEngine


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_CANONICAL_LOCATOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def canonical_locator(value: object, key: str) -> str:
    # Mono #77 owner ACK: untrusted correlation only, never authentication.
    if type(value) is not str or _CANONICAL_LOCATOR_RE.fullmatch(value) is None:
        raise ValueError(f"{key.upper()}_REQUIRED: BLOCKED invalid conversation/message locator")
    return value


def _held_intake_matches(result: object, request: Mapping[str, object]) -> bool:
    """Validate an untrusted, non-authoritative transport response shape."""
    return (
        isinstance(result, dict)
        and result.get("schema") == "PORTAL_DESKTOP_CANONICAL_INTAKE_V1"
        and result.get("state") == "HELD"
        and isinstance(result.get("status"), str)
        and result.get("conversation_id") == request.get("conversation_id")
        and result.get("message_id") == request.get("request_id")
        and result.get("text") == request.get("task")
        and all(result.get(key) is False for key in (
            "origin_authenticated", "model_admitted", "accepted_as_task_result",
            "memory_admitted", "protected_effect_authority"))
    )


class BridgeTimeoutError(TimeoutError):
    pass


class BridgeOutcomeUnknownError(RuntimeError):
    pass


class DesktopRuntimeCommandHandler:
    """Resident-side command handler for the desktop shell.

    It exposes cognition and observable telemetry only. No command in this
    handler grants or executes protected effects.
    """

    def __init__(
        self,
        *,
        engine: ResidentCognitionEngine,
        discover_routes: Callable[[], tuple[CognitionRoute, ...]],
        runtime_status: Callable[[], Mapping[str, object]],
        canonical_runtime: Any = None,
        source_store: Any = None,
    ) -> None:
        self.engine = engine
        self.discover_routes = discover_routes
        self.runtime_status = runtime_status
        self.canonical_runtime = canonical_runtime
        self.source_store = source_store

    @staticmethod
    def _required_text(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    def handle(self, payload: Mapping[str, object], *, outcome_unknown: bool = False) -> dict[str, object]:
        command = self._required_text(payload, "command")
        if command == "desktop_status":
            return dict(self.runtime_status())
        if command == "desktop_discover_routes":
            return {
                "schema": "PORTAL_DESKTOP_COGNITION_ROUTES_V1",
                "routes": [asdict(route) for route in self.discover_routes()],
                "protected_effect_authority": False,
            }
        if command == "desktop_recent_activity":
            limit = int(payload.get("limit", 20))
            items = self.engine.ledger.list_recent(limit=limit)
            return {
                "schema": "PORTAL_DESKTOP_ACTIVITY_V1",
                "items": [
                    {
                        "request_id": row["request_id"],
                        "source": row["source"],
                        "reason": row["reason"],
                        "state": row["state"],
                        "route_id": row["route_id"],
                        "provider": row["provider"],
                        "model_or_agent": row["model_or_agent"],
                        "retryable": bool(row["retryable"]),
                        "error": row["error"],
                        "updated_at": row["updated_at"],
                        "evidence_id": row["evidence_id"],
                        "acceptance": (
                            json.loads(str(row["acceptance_json"]))
                            if row.get("acceptance_json")
                            else None
                        ),
                        "protected_effect_authority": False,
                        "canonical_task_evidence": False,
                        "evidence_kind": "LEGACY_OPERATIONAL_EVIDENCE_ONLY",
                    }
                    for row in items
                ],
            }
        if command == "desktop_conversation_history":
            conversation_id = canonical_locator(payload.get("conversation_id"), "conversation_locator")
            if self.canonical_runtime is None:
                raise ValueError("CANONICAL_INTAKE_UNAVAILABLE: BLOCKED")
            from vera_core.local_conversation import recover_conversation

            try:
                items = recover_conversation(self.canonical_runtime.tasks, self.source_store, conversation_id)
            except AttributeError as exc:
                raise ValueError("CANONICAL_RECOVERY_SOURCE_UNAVAILABLE: BLOCKED") from exc
            return {
                "schema": "PORTAL_DESKTOP_CONVERSATION_HISTORY_V1",
                "conversation_id": conversation_id, "items": items,
                "origin_authenticated": False, "memory_admitted": False,
                "protected_effect_authority": False,
            }
        if command == "desktop_cognize":
            conversation_id = canonical_locator(payload.get("conversation_id"), "conversation_locator")
            request_id = canonical_locator(payload.get("request_id"), "request_id")
            supplied_source = payload.get("source")
            if supplied_source not in (None, "HUMAN"):
                raise ValueError("desktop_cognize source must be HUMAN")
            if self.canonical_runtime is None:
                raise ValueError("CANONICAL_INTAKE_UNAVAILABLE: BLOCKED")
            from vera_core.local_conversation import ConversationError, open_message, recover_message

            # The canonical owner validates the literal bytes and replay before
            # any operational ledger, discovery, acceptance or model transport.
            intake = open_message(self.canonical_runtime.tasks, conversation_id, request_id, payload.get("task"))
            before_head = self.canonical_runtime.tasks.read(intake["task_id"]).journal_head_digest
            try:
                recovery = recover_message(self.canonical_runtime.tasks, self.source_store, intake)
                status = recovery["status"]
            except ConversationError:
                status = "HELD_TASK_INVALIDATED"
            except AttributeError:
                status = "HELD_RECOVERY_SOURCE_UNAVAILABLE"
            current_head = self.canonical_runtime.tasks.read(intake["task_id"]).journal_head_digest
            if current_head != before_head:
                status = "HELD_TASK_CHANGED"
            if outcome_unknown:
                status = "OUTCOME_UNKNOWN_NO_REDISPATCH"
            # No admitted model gate exists at this Desktop seam. An advertised
            # route or old operational COMPLETED row cannot supply admission.
            return {
                **intake, "schema": "PORTAL_DESKTOP_CANONICAL_INTAKE_V1",
                "request_id": request_id, "state": "HELD", "status": status,
                "task_journal_head": current_head,
                "route_id": None, "response_text": None, "retryable": False,
                "error": "no_admitted_provider" if status == "PENDING_NO_ADMITTED_PROVIDER" else status,
                "model_admitted": False, "accepted_as_task_result": False,
                "memory_admitted": False, "protected_effect_authority": False,
            }
        raise ValueError(f"unsupported desktop command: {command!r}")


class FileBridgeClient:
    """Desktop-side client for the existing unified runtime file bridge."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.05,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.runtime_root = Path(runtime_root)
        self.requests = self.runtime_root / "bridge" / "requests"
        self.claimed = self.runtime_root / "bridge" / "claimed"
        self.responses = self.runtime_root / "bridge" / "responses"
        self.timeout_seconds = float(timeout_seconds)
        self.poll_seconds = float(poll_seconds)

    @staticmethod
    def _validate_request_id(request_id: str) -> str:
        if type(request_id) is not str or not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("invalid bridge request_id")
        return request_id

    def unresolved_cognition(self, conversation_id: str) -> tuple[str, str, str] | None:
        """Recover a client's pending ID from existing transport evidence only."""
        canonical_locator(conversation_id, "conversation_locator")
        pending = {}
        for directory in (self.requests, self.claimed):
            for path in directory.glob("*.json"):
                try:
                    original = json.loads(path.read_text(encoding="utf-8-sig"))
                    if not isinstance(original, dict):
                        raise ValueError("invalid claim")
                    if (original.get("command") != "desktop_cognize"
                            or original.get("conversation_id") != conversation_id):
                        continue
                    request_id = canonical_locator(original.get("request_id"), "request_id")
                    text = original.get("task")
                    if (request_id != path.stem or type(text) is not str
                            or original.get("source") not in (None, "HUMAN")):
                        raise ValueError("invalid original message")
                    response_path = self.responses / path.name
                    if response_path.exists():
                        response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                        result = response.get("result", {})
                        if response.get("ok") is False:
                            continue  # Explicit host rejection, not a dispatch retry.
                        if (response.get("request_id") == request_id
                                and _held_intake_matches(result, original)
                                and result.get("status") != "OUTCOME_UNKNOWN_NO_REDISPATCH"
                                ):
                            continue
                    value = (conversation_id, request_id, text)
                    if request_id in pending and pending[request_id] != value:
                        raise ValueError("conflicting original messages")
                    pending[request_id] = value
                except (OSError, UnicodeError, ValueError, AttributeError) as exc:
                    raise BridgeOutcomeUnknownError("unreadable prior transport evidence; BLOCKED") from exc
        if len(pending) > 1:
            raise BridgeOutcomeUnknownError("multiple unresolved prior messages; BLOCKED")
        return next(iter(pending.values()), None)

    def request(
        self,
        command: str,
        *,
        request_id: str | None = None,
        **payload: object,
    ) -> dict[str, object]:
        request_id = self._validate_request_id(uuid.uuid4().hex if request_id is None else request_id)
        canonical = command == "desktop_cognize"
        if canonical:
            canonical_locator(request_id, "request_id")
            canonical_locator(payload.get("conversation_id"), "conversation_locator")
            if payload.get("source") not in (None, "HUMAN"):
                raise ValueError("desktop_cognize source must be HUMAN")
        self.requests.mkdir(parents=True, exist_ok=True)
        self.claimed.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        request_path = self.requests / f"{request_id}.json"
        claimed_path = self.claimed / f"{request_id}.json"
        response_path = self.responses / f"{request_id}.json"
        body = {**payload, "request_id": request_id, "command": command}

        def check_original(path: Path) -> None:
            try:
                original = json.loads(path.read_text(encoding="utf-8-sig"))
                keys = ("command", "request_id", "conversation_id", "task")
                if (not isinstance(original, dict)
                        or any(original.get(k) != body.get(k) for k in keys)
                        or original.get("source") not in (None, "HUMAN")):
                    raise ValueError("bridge request_id binds different original source")
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise BridgeOutcomeUnknownError(f"unreadable original bridge claim: {request_id}") from exc

        exists = (
            request_path.exists()
            or claimed_path.exists()
            or response_path.exists()
        )
        if exists and not canonical:
            raise FileExistsError(f"bridge request_id already exists: {request_id}")
        if exists:
            if claimed_path.exists():
                check_original(claimed_path)
                if not response_path.exists():
                    raise BridgeOutcomeUnknownError(f"claimed but no response; no redispatch: {request_id}")
            elif request_path.exists():
                check_original(request_path)
            else:
                raise BridgeOutcomeUnknownError(f"response has no original claim: {request_id}")
        else:
            temp = request_path.with_name(request_path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temp.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
                # Publish once; concurrent callers cannot replace original bytes.
                if os.name == "nt":
                    # Windows rename is atomic and refuses an existing target.
                    temp.rename(request_path)
                else:
                    os.link(temp, request_path)
            except FileExistsError:
                if not canonical:
                    raise
                check_original(request_path)
            finally:
                temp.unlink(missing_ok=True)

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if response_path.is_file():
                try:
                    response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                except (json.JSONDecodeError, UnicodeError, FileNotFoundError):
                    time.sleep(self.poll_seconds)
                    continue
                if not canonical:
                    response_path.unlink(missing_ok=True)
                if not isinstance(response, dict):
                    if canonical:
                        raise BridgeOutcomeUnknownError(f"unverified bridge response; no redispatch: {request_id}")
                    raise RuntimeError("bridge response is not an object")
                if response.get("request_id") != request_id:
                    if canonical:
                        raise BridgeOutcomeUnknownError(f"response identity differs; no redispatch: {request_id}")
                    raise RuntimeError("bridge response request_id mismatch")
                if response.get("ok") is not True:
                    raise RuntimeError(
                        str(response.get("error") or "bridge request failed")
                    )
                result = response.get("result")
                if not isinstance(result, dict):
                    if canonical:
                        raise BridgeOutcomeUnknownError(f"unverified canonical result; no redispatch: {request_id}")
                    raise RuntimeError("bridge result is not an object")
                if canonical:
                    # Retain original transport claim and response as evidence.
                    # A replayed response is historical; use canonical history
                    # for fresh task-head/recovery evidence.
                    check_original(claimed_path)
                    if not _held_intake_matches(result, body):
                        raise BridgeOutcomeUnknownError(f"unverified canonical response; no redispatch: {request_id}")
                    return {**result, "transport_replayed": exists, "task_head_current": False}
                return result
            time.sleep(self.poll_seconds)

        if response_path.exists():
            raise BridgeOutcomeUnknownError(f"incomplete bridge response; no redispatch: {request_id}")
        try:
            request_path.unlink()
        except FileNotFoundError:
            raise BridgeOutcomeUnknownError(
                f"bridge request was claimed but no response arrived: {request_id}"
            )
        raise BridgeTimeoutError(
            f"bridge request timed out before host claim: {request_id}"
        )
