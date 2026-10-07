from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import time
from typing import Callable, Mapping
import uuid

from .desktop_cognition import CognitionRoute
from .desktop_runtime import (
    CognitionRequestEnvelope,
    ResidentCognitionEngine,
)
from .desktop_supervisor import _launch_guard


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


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
    ) -> None:
        self.engine = engine
        self.discover_routes = discover_routes
        self.runtime_status = runtime_status

    @staticmethod
    def _required_text(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
        return value.strip()

    def handle(self, payload: Mapping[str, object]) -> dict[str, object]:
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
                    }
                    for row in items
                ],
            }
        if command == "desktop_cognize":
            request_id = self._required_text(payload, "request_id")
            supplied_source = payload.get("source")
            if supplied_source not in (None, "HUMAN"):
                raise ValueError("desktop_cognize source must be HUMAN")
            source = "HUMAN"
            reason = self._required_text(payload, "reason")
            task = self._required_text(payload, "task")
            created_at = time.time()
            raw_capabilities = payload.get("required_capabilities", ["text"])
            if not isinstance(raw_capabilities, list) or not all(
                isinstance(item, str) and item.strip()
                for item in raw_capabilities
            ):
                raise ValueError("required_capabilities must be a list of strings")
            result = self.engine.process(
                CognitionRequestEnvelope(
                    request_id=request_id,
                    source=source,
                    reason=reason,
                    task=task,
                    created_at=created_at,
                    required_capabilities=tuple(
                        item.strip() for item in raw_capabilities
                    ),
                )
            )
            rendered = asdict(result)
            rendered["protected_effect_authority"] = False
            return rendered
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
        self.cancelled = self.runtime_root / "bridge" / "cancelled"
        self.submissions = self.runtime_root / "bridge" / "submissions"
        self.timeout_seconds = float(timeout_seconds)
        self.poll_seconds = float(poll_seconds)

    @staticmethod
    def _validate_request_id(request_id: str) -> str:
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("invalid bridge request_id")
        return request_id

    @staticmethod
    def _identity(body: Mapping[str, object]) -> str:
        # The resident host owns timestamps. Caller clock changes on a retry
        # must not make an otherwise identical request a different operation.
        return json.dumps(
            {key: value for key, value in body.items() if key != "created_at"},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _submit_once(self, body: dict[str, object], request_path: Path) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            # A short OS-held file lock releases automatically if a submitter
            # dies; both GUI instances and retry processes share it.
            with _launch_guard(self.submissions / (request_path.name + ".lock")) as acquired:
                if acquired:
                    self._publish_or_validate(body, request_path)
                    return
            time.sleep(self.poll_seconds)
        raise BridgeOutcomeUnknownError("bridge submission lock timed out")

    def _publish_or_validate(self, body: dict[str, object], request_path: Path) -> None:
        retained_paths = [
            directory / request_path.name
            for directory in (self.submissions, self.requests, self.claimed, self.cancelled)
        ]
        for path in retained_paths:
            try:
                prior = json.loads(path.read_text(encoding="utf-8-sig"))
            except FileNotFoundError:
                continue
            if not isinstance(prior, dict) or self._identity(prior) != self._identity(body):
                raise ValueError("bridge request_id already binds a different payload")
            return
        if (self.responses / request_path.name).exists():
            raise ValueError("bridge response has no retained request identity")

        temp = self.submissions / (request_path.name + "." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.replace(temp, retained_paths[0])
            temp.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temp, request_path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _response(path: Path, request_id: str) -> dict[str, object] | None:
        try:
            response = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        if not isinstance(response, dict):
            raise RuntimeError("bridge response is not an object")
        if response.get("request_id") != request_id:
            raise RuntimeError("bridge response request_id mismatch")
        if response.get("ok") is not True:
            raise RuntimeError(str(response.get("error") or "bridge request failed"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("bridge result is not an object")
        return result

    def request(
        self,
        command: str,
        *,
        request_id: str | None = None,
        **payload: object,
    ) -> dict[str, object]:
        request_id = self._validate_request_id(
            request_id or uuid.uuid4().hex
        )
        for directory in (self.requests, self.claimed, self.responses, self.cancelled, self.submissions):
            directory.mkdir(parents=True, exist_ok=True)
        request_path = self.requests / f"{request_id}.json"
        response_path = self.responses / f"{request_id}.json"
        cancelled_path = self.cancelled / f"{request_id}.json"

        body = {
            **payload,
            "request_id": request_id,
            "command": command,
        }
        self._submit_once(body, request_path)
        if cancelled_path.exists():
            raise BridgeTimeoutError(f"bridge request timed out before host claim: {request_id}")

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            result = self._response(response_path, request_id)
            if result is not None:
                return result
            time.sleep(self.poll_seconds)

        try:
            # Host claim and cancellation compete for the same atomic rename.
            # Only one can win; a request removed by its host is never called
            # cancelled or silently resubmitted by this client.
            os.replace(request_path, cancelled_path)
        except FileNotFoundError:
            result = self._response(response_path, request_id)
            if result is not None:
                return result
            if cancelled_path.exists():
                raise BridgeTimeoutError(
                    f"bridge request timed out before host claim: {request_id}"
                )
            raise BridgeOutcomeUnknownError(
                f"bridge request was claimed but no response arrived: {request_id}"
            )
        raise BridgeTimeoutError(
            f"bridge request timed out before host claim: {request_id}"
        )
