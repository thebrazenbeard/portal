from __future__ import annotations

from dataclasses import asdict
import json
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
            source = self._required_text(payload, "source")
            reason = self._required_text(payload, "reason")
            task = self._required_text(payload, "task")
            created_at = float(payload.get("created_at", time.time()))
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
        self.responses = self.runtime_root / "bridge" / "responses"
        self.timeout_seconds = float(timeout_seconds)
        self.poll_seconds = float(poll_seconds)

    @staticmethod
    def _validate_request_id(request_id: str) -> str:
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("invalid bridge request_id")
        return request_id

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
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        request_path = self.requests / f"{request_id}.json"
        response_path = self.responses / f"{request_id}.json"
        if request_path.exists() or response_path.exists():
            raise FileExistsError(f"bridge request_id already exists: {request_id}")

        body = {
            **payload,
            "request_id": request_id,
            "command": command,
        }
        temp = request_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temp.replace(request_path)

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if response_path.is_file():
                response = json.loads(
                    response_path.read_text(encoding="utf-8-sig")
                )
                response_path.unlink(missing_ok=True)
                if not isinstance(response, dict):
                    raise RuntimeError("bridge response is not an object")
                if response.get("request_id") != request_id:
                    raise RuntimeError("bridge response request_id mismatch")
                if response.get("ok") is not True:
                    raise RuntimeError(
                        str(response.get("error") or "bridge request failed")
                    )
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("bridge result is not an object")
                return result
            time.sleep(self.poll_seconds)

        if request_path.exists():
            request_path.unlink(missing_ok=True)
            raise BridgeTimeoutError(
                f"bridge request timed out before host claim: {request_id}"
            )
        raise BridgeOutcomeUnknownError(
            f"bridge request was claimed but no response arrived: {request_id}"
        )
