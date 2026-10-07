from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable
import urllib.request

from .desktop_cognition import (
    CognitionRequest,
    CognitionRoute,
    discover_cognition_routes,
    select_cognition_route,
    loopback_base_url,
    loopback_json,
    ollama_model_is_remote,
    route_is_current,
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
    provider: str | None = None
    model_or_agent: str | None = None
    acceptance: dict[str, object] | None = None


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
        for column in ("route_json", "required_capabilities_json"):
            if column not in columns:
                self.connection.execute(f"ALTER TABLE desktop_cognition_runs ADD COLUMN {column} TEXT")

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
            "reason": request.reason,
            "task": request.task,
            "created_at": request.created_at,
            "state": state,
            "route_id": route.route_id if route else None,
            "provider": route.provider if route else None,
            "model_or_agent": route.model_or_agent if route else None,
            "route": asdict(route) if route else None,
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
                started_at, completed_at, updated_at, evidence_id,
                route_json, required_capabilities_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)
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
                evidence_id=excluded.evidence_id,
                route_json=excluded.route_json,
                required_capabilities_json=excluded.required_capabilities_json
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
                json.dumps(asdict(route), sort_keys=True) if route else None,
                json.dumps(list(request.required_capabilities)),
            ),
        )
        return evidence_id


RouteDiscovery = Callable[[], tuple[CognitionRoute, ...]]
RouteInvoker = Callable[[CognitionRoute, str], str]


def invoke_local_text(route: CognitionRoute, task: str) -> str:
    if (not route.available or not route_is_current(route) or not route.local
            or route.incremental_paid_compute is not False
            or route.effect_authority_ceiling != "COGNITION_ONLY_NO_PROTECTED_EFFECT"):
        raise RuntimeError("resident adapter requires a current local no-paid cognition route")
    if route.provider == "ollama":
        base_url = loopback_base_url(route.base_url or "http://127.0.0.1:11434")
        inventory = loopback_json(urllib.request.Request(f"{base_url}/api/tags"), timeout=2.0)
        models = inventory.get("models")
        matches = [item for item in models if isinstance(item, dict)
                   and item.get("name") == route.model_or_agent] if isinstance(models, list) else []
        if len(matches) != 1 or ollama_model_is_remote(matches[0], route.model_or_agent):
            raise RuntimeError("Ollama route is no longer a unique local model")
        item = matches[0]
        current_version = item.get("digest") or item.get("modified_at")
        if route.observed_version and current_version != route.observed_version:
            raise RuntimeError("Ollama model binding drifted since discovery")
        show_request = urllib.request.Request(
            f"{base_url}/api/show",
            data=json.dumps({"model": route.model_or_agent}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        metadata = loopback_json(show_request, timeout=2.0)
        if ollama_model_is_remote(metadata, route.model_or_agent):
            raise RuntimeError("Ollama model metadata reports a remote model")
        payload = json.dumps(
            {
                "model": route.model_or_agent,
                "prompt": task,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        body = loopback_json(request, timeout=120.0)
        if not isinstance(body, dict) or body.get("done") is not True:
            raise RuntimeError("Ollama response did not complete")
        text = body.get("response")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Ollama response was empty")
        return text.strip()

    if route.provider in {"pre_active_local", "pre_active_target"}:
        base_url = loopback_base_url(route.base_url or (
            "http://127.0.0.1:18081/v1" if route.provider == "pre_active_local" else None
        ))
        headers = {"Content-Type": "application/json"}
        if route.api_key_env:
            token = os.environ.get(route.api_key_env)
            if not token:
                raise RuntimeError("Pre-Active target API key environment is not populated")
            headers["Authorization"] = f"Bearer {token}"
        advertised = loopback_json(urllib.request.Request(f"{base_url}/models", headers=headers), timeout=2.0)
        models = advertised.get("data")
        entries = [item for item in models if isinstance(item, dict) and item.get("id") == route.model_or_agent] if isinstance(models, list) else []
        if len(entries) != 1:
            raise RuntimeError("Pre-Active model binding is no longer available")
        version = entries[0].get("adapter_model_sha256") or entries[0].get("base_model_revision")
        if route.observed_version and version != route.observed_version:
            raise RuntimeError("Pre-Active model binding drifted since discovery")
        payload = json.dumps(
            {
                "model": route.model_or_agent,
                "messages": [{"role": "user", "content": task}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers=headers,
        )
        body = loopback_json(request, timeout=180.0)
        if not isinstance(body, dict):
            raise RuntimeError("Pre-Active model response is not an object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Pre-Active model response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("Pre-Active model choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Pre-Active model choice has no message")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Pre-Active model response was empty")
        return text.strip()

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
        authorized_paid_route_ids: frozenset[str] = frozenset(),
        accept_result: Callable[[CognitionRequestEnvelope, CognitionRoute, str], dict[str, object]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.discover_routes = discover_routes
        self.invoke_route = invoke_route
        self.authorized_route_ids = authorized_route_ids
        self.authorized_paid_route_ids = authorized_paid_route_ids
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
            provider=str(row["provider"]) if row["provider"] is not None else None,
            model_or_agent=str(row["model_or_agent"]) if row["model_or_agent"] is not None else None,
            acceptance=json.loads(str(row["acceptance_json"])) if row.get("acceptance_json") else None,
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
        if stored is not None and stored.get("required_capabilities_json") is not None and json.loads(str(stored["required_capabilities_json"])) != list(request.required_capabilities):
            raise ValueError("request_id already binds different cognition capabilities")
        if stored is not None and stored["state"] == "COMPLETED":
            return self._result_from_stored(stored)

        # Persisted output waiting on acceptance is retried at the intake
        # boundary. Re-invoking the model would duplicate already observed work.
        response_text = stored.get("response_text") if stored else None
        saved_route = stored.get("route_json") if stored else None
        route = CognitionRoute(**json.loads(str(saved_route))) if saved_route and response_text else None
        if route is None:
            routes = self.discover_routes()
            route = select_cognition_route(
            CognitionRequest(
                required_capabilities=request.required_capabilities,
            ),
            routes,
            authorized_route_ids=self.authorized_route_ids,
            authorized_paid_route_ids=self.authorized_paid_route_ids,
            now=observed_now,
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

        started_at = float(stored["started_at"]) if stored and stored.get("started_at") is not None and response_text else observed_now
        self.ledger.store(
            request,
            state="RUNNING",
            retryable=True,
            now=observed_now,
            route=route,
            response_text=response_text if isinstance(response_text, str) else None,
            started_at=started_at,
        )
        try:
            if response_text is None:
                response_text = self.invoke_route(route, request.task)
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("cognition response must be nonempty text")
            self.ledger.store(request, state="RUNNING", retryable=True,
                              now=observed_now, route=route,
                              response_text=response_text, started_at=started_at)
            acceptance = (
                self.accept_result(request, route, response_text)
                if self.accept_result else {"status": "UNQUALIFIED_LEDGER_ONLY"}
            )
            self.ledger.connection.execute(
                "UPDATE desktop_cognition_runs SET acceptance_json=? WHERE request_id=?",
                (json.dumps(acceptance, sort_keys=True), request.request_id),
            )
            if (not isinstance(acceptance, dict)
                    or (self.accept_result is not None and acceptance.get("status") not in {"ACCEPTED_OBSERVATION", "ACCEPTED_HOST_OBSERVATION"})
                    or acceptance.get("protected_effect_authority", False) is not False
                    or acceptance.get("canonical_memory_write", False) is not False):
                raise RuntimeError("Vera intake rejected cognition result")
        except Exception as exc:
            observed_now = float(time.time() if now is None else now)
            error = f"{type(exc).__name__}: {exc}"
            evidence_id = self.ledger.store(
                request,
                state="FAILED",
                retryable=True,
                now=observed_now,
                route=route,
                response_text=response_text if isinstance(response_text, str) else None,
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
                provider=route.provider,
                model_or_agent=route.model_or_agent,
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
            provider=route.provider,
            model_or_agent=route.model_or_agent,
            acceptance=acceptance,
        )
