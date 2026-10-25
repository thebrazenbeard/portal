from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable

from .desktop_cognition import (
    CognitionRequest,
    CognitionRoute,
    discover_cognition_routes,
    select_cognition_route,
)


@dataclass(frozen=True)
class CognitionRequestEnvelope:
    request_id: str
    source: str
    reason: str
    task: str
    created_at: float
    required_capabilities: tuple[str, ...] = ("text",)


@dataclass(frozen=True)
class CognitionResult:
    request_id: str
    state: str
    route_id: str | None
    response_text: str | None
    retryable: bool
    evidence_id: str
    error: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_cognition_runs (
    request_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    task TEXT NOT NULL,
    created_at REAL NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('UNRESOLVED','RUNNING','COMPLETED','FAILED')),
    retryable INTEGER NOT NULL CHECK(retryable IN (0,1)),
    route_id TEXT,
    provider TEXT,
    model_or_agent TEXT,
    response_text TEXT,
    error TEXT,
    protected_effect_authority INTEGER NOT NULL CHECK(protected_effect_authority = 0),
    started_at REAL,
    completed_at REAL,
    updated_at REAL NOT NULL,
    evidence_id TEXT NOT NULL
);
"""


class CognitionLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(_SCHEMA)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(desktop_cognition_runs)")}
        if "acceptance_json" not in columns:
            self.connection.execute("ALTER TABLE desktop_cognition_runs ADD COLUMN acceptance_json TEXT")

    def close(self) -> None:
        self.connection.close()

    def get(self, request_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM desktop_cognition_runs WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> list[dict[str, object]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            """
            SELECT *
            FROM desktop_cognition_runs
            ORDER BY updated_at DESC, request_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def store(
        self,
        request: CognitionRequestEnvelope,
        *,
        state: str,
        retryable: bool,
        now: float,
        route: CognitionRoute | None = None,
        response_text: str | None = None,
        error: str | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
    ) -> str:
        evidence_payload = {
            "schema": "PORTAL_DESKTOP_COGNITION_EVIDENCE_V1",
            "request_id": request.request_id,
            "source": request.source,
            "state": state,
            "route_id": route.route_id if route else None,
            "provider": route.provider if route else None,
            "model_or_agent": route.model_or_agent if route else None,
            "response_text": response_text,
            "error": error,
            "protected_effect_authority": False,
        }
        evidence_id = "cognition:" + hashlib.sha256(
            json.dumps(
                evidence_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self.connection.execute(
            """
            INSERT INTO desktop_cognition_runs(
                request_id, source, reason, task, created_at,
                state, retryable, route_id, provider, model_or_agent,
                response_text, error, protected_effect_authority,
                started_at, completed_at, updated_at, evidence_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)
            ON CONFLICT(request_id) DO UPDATE SET
                source=excluded.source,
                reason=excluded.reason,
                task=excluded.task,
                created_at=excluded.created_at,
                state=excluded.state,
                retryable=excluded.retryable,
                route_id=excluded.route_id,
                provider=excluded.provider,
                model_or_agent=excluded.model_or_agent,
                response_text=excluded.response_text,
                error=excluded.error,
                protected_effect_authority=0,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at,
                updated_at=excluded.updated_at,
                evidence_id=excluded.evidence_id
            """,
            (
                request.request_id,
                request.source,
                request.reason,
                request.task,
                request.created_at,
                state,
                int(retryable),
                route.route_id if route else None,
                route.provider if route else None,
                route.model_or_agent if route else None,
                response_text,
                error,
                started_at,
                completed_at,
                now,
                evidence_id,
            ),
        )
        return evidence_id


RouteDiscovery = Callable[[], tuple[CognitionRoute, ...]]
RouteInvoker = Callable[[CognitionRoute, str], str]

_MAX_LOCAL_PROMPT_BYTES = 16 * 1024
_MAX_LOCAL_RESPONSE_BYTES = 256 * 1024
_MAX_LOCAL_RESPONSE_CHARS = 32 * 1024


def _post_loopback_json(
    *, port: int, path: str, model: str, task: str, payload: dict[str, object],
    timeout_seconds: float,
) -> dict[str, object]:
    """Use a direct numeric-loopback connection: never a proxy or redirect."""
    if not isinstance(model, str) or not model.strip() or len(model.encode("utf-8")) > 128:
        raise ValueError("local model identifier is missing or too long")
    if not isinstance(task, str) or not task.strip() or len(task.encode("utf-8")) > _MAX_LOCAL_PROMPT_BYTES:
        raise ValueError("local cognition prompt is missing or too large")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _MAX_LOCAL_PROMPT_BYTES + 1024:
        raise ValueError("local model request is too large")

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_seconds)
    try:
        connection.request(
            "POST", path, body=encoded,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"local model HTTP status {response.status}")
        content = response.read(_MAX_LOCAL_RESPONSE_BYTES + 1)
        if len(content) > _MAX_LOCAL_RESPONSE_BYTES:
            raise RuntimeError("local model response exceeded size limit")
    finally:
        connection.close()

    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("local model response is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("local model response is not an object")
    if data.get("model") != model:
        raise RuntimeError("local model response identity mismatch")
    if "tool_calls" in data or "function_call" in data:
        raise RuntimeError("local model attempted a tool call")
    return data


def _local_answer_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("local model response was empty")
    text = value.strip()
    if len(text) > _MAX_LOCAL_RESPONSE_CHARS:
        raise RuntimeError("local model response text exceeded size limit")
    return text


def invoke_local_text(route: CognitionRoute, task: str) -> str:
    if route.provider == "ollama":
        body = _post_loopback_json(
            port=11434,
            path="/api/generate",
            model=route.model_or_agent,
            task=task,
            timeout_seconds=60.0,
            payload={"model": route.model_or_agent, "prompt": task, "stream": False},
        )
        if body.get("done") is not True:
            raise RuntimeError("Ollama response did not complete")
        return _local_answer_text(body.get("response"))

    if route.provider == "pre_active_local":
        body = _post_loopback_json(
            port=18081,
            path="/v1/chat/completions",
            model=route.model_or_agent,
            task=task,
            timeout_seconds=90.0,
            payload={
                "model": route.model_or_agent,
                "messages": [{"role": "user", "content": task}],
            },
        )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Pre-Active model response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("Pre-Active model choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Pre-Active model choice has no message")
        if (
            "tool_calls" in message
            or "function_call" in message
            or first.get("finish_reason") == "tool_calls"
        ):
            raise RuntimeError("local model attempted a tool call")
        return _local_answer_text(message.get("content"))

    raise RuntimeError(f"no resident adapter for provider: {route.provider}")


# Compatibility alias for callers that explicitly import the old helper.
invoke_ollama_text = invoke_local_text


class ResidentCognitionEngine:
    def __init__(
        self,
        *,
        ledger: CognitionLedger,
        discover_routes: RouteDiscovery = discover_cognition_routes,
        invoke_route: RouteInvoker = invoke_local_text,
        authorized_route_ids: frozenset[str] = frozenset(),
        accept_result: Callable[[CognitionRequestEnvelope, CognitionRoute, str], dict[str, object]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.discover_routes = discover_routes
        self.invoke_route = invoke_route
        self.authorized_route_ids = authorized_route_ids
        self.accept_result = accept_result

    @staticmethod
    def _result_from_stored(row: dict[str, object]) -> CognitionResult:
        return CognitionResult(
            request_id=str(row["request_id"]),
            state=str(row["state"]),
            route_id=str(row["route_id"]) if row["route_id"] is not None else None,
            response_text=(
                str(row["response_text"])
                if row["response_text"] is not None
                else None
            ),
            retryable=bool(row["retryable"]),
            evidence_id=str(row["evidence_id"]),
            error=str(row["error"]) if row["error"] is not None else None,
        )

    def process(
        self,
        request: CognitionRequestEnvelope,
        *,
        now: float | None = None,
    ) -> CognitionResult:
        observed_now = float(time.time() if now is None else now)
        stored = self.ledger.get(request.request_id)
        if stored is not None and any(stored[key] != getattr(request, key) for key in ("source", "reason", "task")):
            raise ValueError("request_id already binds a different cognition request")
        if stored is not None and stored["state"] == "COMPLETED":
            return self._result_from_stored(stored)

        routes = self.discover_routes()
        route = select_cognition_route(
            CognitionRequest(
                required_capabilities=request.required_capabilities,
            ),
            routes,
            authorized_route_ids=self.authorized_route_ids,
        )
        if route is None:
            evidence_id = self.ledger.store(
                request,
                state="UNRESOLVED",
                retryable=True,
                now=observed_now,
                error="no_admissible_cognition_route",
                completed_at=observed_now,
            )
            return CognitionResult(
                request_id=request.request_id,
                state="UNRESOLVED",
                route_id=None,
                response_text=None,
                retryable=True,
                evidence_id=evidence_id,
                error="no_admissible_cognition_route",
            )

        started_at = observed_now
        self.ledger.store(
            request,
            state="RUNNING",
            retryable=True,
            now=observed_now,
            route=route,
            started_at=started_at,
        )
        try:
            response_text = self.invoke_route(route, request.task)
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("cognition response must be nonempty text")
            acceptance = (
                self.accept_result(request, route, response_text)
                if self.accept_result else {"status": "UNQUALIFIED_LEDGER_ONLY"}
            )
            self.ledger.connection.execute(
                "UPDATE desktop_cognition_runs SET acceptance_json=? WHERE request_id=?",
                (json.dumps(acceptance, sort_keys=True), request.request_id),
            )
        except Exception as exc:
            observed_now = float(time.time() if now is None else now)
            error = f"{type(exc).__name__}: {exc}"
            evidence_id = self.ledger.store(
                request,
                state="FAILED",
                retryable=True,
                now=observed_now,
                route=route,
                error=error,
                started_at=started_at,
                completed_at=observed_now,
            )
            return CognitionResult(
                request_id=request.request_id,
                state="FAILED",
                route_id=route.route_id,
                response_text=None,
                retryable=True,
                evidence_id=evidence_id,
                error=error,
            )

        observed_now = float(time.time() if now is None else now)
        evidence_id = self.ledger.store(
            request,
            state="COMPLETED",
            retryable=False,
            now=observed_now,
            route=route,
            response_text=response_text,
            started_at=started_at,
            completed_at=observed_now,
        )
        return CognitionResult(
            request_id=request.request_id,
            state="COMPLETED",
            route_id=route.route_id,
            response_text=response_text,
            retryable=False,
            evidence_id=evidence_id,
        )
