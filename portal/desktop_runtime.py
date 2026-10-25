from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable

from .desktop_http_deadline import _DeadlineHTTPConnection
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
    evidence_id TEXT NOT NULL,
    conversation_binding_digest TEXT,
    acceptance_json TEXT,
    required_capabilities_json TEXT,
    attempt_token TEXT,
    acceptance_claimed INTEGER NOT NULL DEFAULT 0
);
"""


class CognitionLedger:
    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        """Tolerate only transient WAL switch contention during concurrent boot.

        Windows SQLite may report SQLITE_BUSY immediately on the mode-changing
        PRAGMA even with a connection busy timeout. The retry is scoped to
        this PRAGMA, bounded by monotonic time, and never masks other I/O errors.
        """
        deadline = time.monotonic() + 2.0
        while True:
            try:
                row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if row is None or str(row[0]).lower() != "wal":
                    raise RuntimeError("SQLite WAL journal mode unavailable")
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                busy = (isinstance(code, int) and (code & 0xFF) in (
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED
                )) or (
                    code is None and str(exc).lower() == "database is locked"
                )
                remaining = deadline - time.monotonic()
                if not busy or remaining <= 0:
                    raise
                time.sleep(min(0.02, remaining))

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
        )
        self.connection.row_factory = sqlite3.Row
        try:
            self._enable_wal(self.connection)
        except Exception:
            self.connection.close()
            raise
        self.connection.executescript(_SCHEMA)
        # Serialize additive upgrades for two hosts starting on the same
        # preexisting SQLite file. Reinspect *under* the write lock so a
        # second opener cannot duplicate an ALTER run by the first.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(desktop_cognition_runs)")
            }
            upgrades = {
                "acceptance_json": "TEXT",
                "conversation_binding_digest": "TEXT",
                "required_capabilities_json": "TEXT",
                "attempt_token": "TEXT",
                "acceptance_claimed": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in upgrades.items():
                if name not in columns:
                    self.connection.execute(
                        f"ALTER TABLE desktop_cognition_runs ADD COLUMN {name} {declaration}"
                    )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

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

    @staticmethod
    def _capabilities_json(request: CognitionRequestEnvelope) -> str:
        capabilities = request.required_capabilities
        if not isinstance(capabilities, tuple) or not capabilities or any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in capabilities
        ):
            raise ValueError("invalid cognition capability requirements")
        try:
            for value in capabilities:
                value.encode("utf-8")
            return json.dumps(sorted(set(capabilities)), ensure_ascii=False)
        except (UnicodeEncodeError, TypeError) as exc:
            raise ValueError("invalid cognition capability requirements") from exc

    @staticmethod
    def _evidence(
        request: CognitionRequestEnvelope, *, state: str,
        route: CognitionRoute | None, response_text: str | None,
        error: str | None, conversation_binding_digest: str | None,
    ) -> str:
        evidence_payload = {
            "schema": "PORTAL_DESKTOP_COGNITION_EVIDENCE_V2",
            "request_id": request.request_id,
            "source": request.source,
            "reason": request.reason,
            "task": request.task,
            "created_at": request.created_at,
            "required_capabilities": json.loads(CognitionLedger._capabilities_json(request)),
            "state": state,
            "route_id": route.route_id if route else None,
            "provider": route.provider if route else None,
            "model_or_agent": route.model_or_agent if route else None,
            "response_text": response_text,
            "error": error,
            "protected_effect_authority": False,
            "conversation_binding_digest": conversation_binding_digest,
        }
        return "cognition:" + hashlib.sha256(
            json.dumps(
                evidence_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def claim(
        self, request: CognitionRequestEnvelope, *, now: float,
        conversation_binding_digest: str | None,
    ) -> tuple[str | None, dict[str, object] | None]:
        """Atomically assign one attempt across separate SQLite connections.

        A legacy row lacking a capability identity is never silently upgraded.
        RUNNING rows need explicit reconciliation after owner failure; no wall-clock
        expiry can steal an attempt while model/acceptance work may still run.
        """
        capabilities_json = self._capabilities_json(request)
        token = secrets.token_hex(16)
        evidence_id = self._evidence(
            request, state="RUNNING", route=None, response_text=None,
            error=None, conversation_binding_digest=conversation_binding_digest,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(request.request_id)
            if row is not None:
                if row["conversation_binding_digest"] != conversation_binding_digest:
                    raise ValueError("request_id already binds a different conversation session")
                if (
                    row["required_capabilities_json"] != capabilities_json
                    or any(row[key] != getattr(request, key) for key in (
                        "source", "reason", "task", "created_at"
                    ))
                ):
                    raise ValueError("request_id already binds a different cognition request")
                if row["state"] in ("COMPLETED", "RUNNING") or not row["retryable"]:
                    self.connection.execute("COMMIT")
                    return None, row
                cursor = self.connection.execute(
                    """UPDATE desktop_cognition_runs SET state='RUNNING', retryable=1,
                        route_id=NULL, provider=NULL, model_or_agent=NULL,
                        response_text=NULL, error=NULL, started_at=?, completed_at=NULL,
                        updated_at=?, evidence_id=?, attempt_token=?, acceptance_claimed=0,
                        acceptance_json=NULL
                       WHERE request_id=? AND state IN ('FAILED','UNRESOLVED') AND retryable=1""",
                    (now, now, evidence_id, token, request.request_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("request claim changed unexpectedly")
            else:
                self.connection.execute(
                    """INSERT INTO desktop_cognition_runs(
                        request_id, source, reason, task, created_at,
                        required_capabilities_json, state, retryable,
                        protected_effect_authority, started_at, updated_at,
                        evidence_id, conversation_binding_digest, attempt_token
                    ) VALUES(?,?,?,?,?,?,'RUNNING',1,0,?,?,?,?,?)""",
                    (request.request_id, request.source, request.reason, request.task,
                     request.created_at, capabilities_json, now, now, evidence_id,
                     conversation_binding_digest, token),
                )
            self.connection.execute("COMMIT")
            return token, None
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def begin_acceptance(self, request_id: str, token: str) -> None:
        """Reserve the sole acceptance callback before invoking user code.

        Ambiguous acceptance outcome remains nonretryable across restart.
        """
        cursor = self.connection.execute(
            """UPDATE desktop_cognition_runs SET acceptance_claimed=1, retryable=0
               WHERE request_id=? AND attempt_token=? AND state='RUNNING'
                 AND acceptance_claimed=0""",
            (request_id, token),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("cognition attempt is no longer current")

    def reconcile_abandoned(
        self, request_id: str, expected_attempt_token: str, *,
        confirmed_owner_stopped: bool,
    ) -> bool:
        """Explicitly release an unaccepted orphan after external owner-stop proof.

        No timer expires live model work; the caller must verify that the old
        host cannot still claim acceptance. The token fences a late owner even
        if that external confirmation was mistaken. Once acceptance began,
        the outcome is ambiguous and this API refuses a retry.
        """
        if not confirmed_owner_stopped:
            raise ValueError("explicit owner confirmation is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(request_id)
            if row is None or row["state"] != "RUNNING" or row["attempt_token"] != expected_attempt_token:
                self.connection.execute("COMMIT")
                return False
            if row["acceptance_claimed"]:
                raise ValueError("acceptance outcome unknown; external reconciliation required")
            if row["required_capabilities_json"] is None:
                raise ValueError("legacy request identity is not qualified for reconciliation")
            request = CognitionRequestEnvelope(
                request_id=str(row["request_id"]), source=str(row["source"]),
                reason=str(row["reason"]), task=str(row["task"]),
                created_at=float(row["created_at"]),
                required_capabilities=tuple(json.loads(str(row["required_capabilities_json"]))),
            )
            error = "operator_reconciled_unaccepted_attempt"
            evidence_id = self._evidence(
                request, state="FAILED", route=None, response_text=None,
                error=error, conversation_binding_digest=row["conversation_binding_digest"],
            )
            observed_now = time.time()
            cursor = self.connection.execute(
                """UPDATE desktop_cognition_runs SET state='FAILED', retryable=1,
                    error=?, completed_at=?, updated_at=?, evidence_id=?, attempt_token=NULL
                   WHERE request_id=? AND attempt_token=? AND state='RUNNING' AND acceptance_claimed=0""",
                (error, observed_now, observed_now, evidence_id, request_id, expected_attempt_token),
            )
            self.connection.execute("COMMIT")
            return cursor.rowcount == 1
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def finish(
        self, request: CognitionRequestEnvelope, *, attempt_token: str,
        state: str, retryable: bool, now: float,
        route: CognitionRoute | None = None, response_text: str | None = None,
        error: str | None = None,
        completed_at: float | None = None,
        conversation_binding_digest: str | None = None,
        acceptance: dict[str, object] | None = None,
        acceptance_claimed: bool = False,
    ) -> str:
        if state not in ("COMPLETED", "UNRESOLVED", "FAILED"):
            raise ValueError("invalid terminal cognition state")
        if state == "COMPLETED" and not acceptance_claimed:
            raise ValueError("completion requires a reserved acceptance")
        if acceptance_claimed and (retryable or state == "UNRESOLVED"):
            raise ValueError("reserved acceptance cannot be retryable or unresolved")
        if state == "COMPLETED" and retryable:
            raise ValueError("completed cognition cannot be retryable")
        evidence_id = self._evidence(
            request, state=state, route=route, response_text=response_text,
            error=error, conversation_binding_digest=conversation_binding_digest,
        )
        cursor = self.connection.execute(
            """UPDATE desktop_cognition_runs SET state=?, retryable=?, route_id=?,
                provider=?, model_or_agent=?, response_text=?, error=?,
                completed_at=?, updated_at=?, evidence_id=?, acceptance_json=?
               WHERE request_id=? AND attempt_token=? AND state='RUNNING'
                 AND acceptance_claimed=?""",
            (state, int(retryable), route.route_id if route else None,
             route.provider if route else None, route.model_or_agent if route else None,
             response_text, error, completed_at, now, evidence_id,
             json.dumps(acceptance, sort_keys=True) if acceptance is not None else None,
             request.request_id, attempt_token, int(acceptance_claimed)),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("cognition attempt is no longer current")
        return evidence_id


RouteDiscovery = Callable[[], tuple[CognitionRoute, ...]]
RouteInvoker = Callable[[CognitionRoute, str], str]
ChatInvoker = Callable[[CognitionRoute, list[dict[str, str]]], str]

_MAX_LOCAL_PROMPT_BYTES = 16 * 1024
_MAX_LOCAL_RESPONSE_BYTES = 256 * 1024
_MAX_LOCAL_RESPONSE_CHARS = 32 * 1024
_MAX_CONTEXT_PAIRS = 6
_MAX_CONTEXT_BYTES = 6 * 1024
_MAX_CONTEXT_SESSIONS = 32


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

    connection = _DeadlineHTTPConnection("127.0.0.1", port, timeout=timeout_seconds)
    response = None
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
        try:
            if response is not None:
                response.close()
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
            timeout_seconds=120.0,
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
            timeout_seconds=180.0,
            payload={
                "model": route.model_or_agent,
                "messages": [{"role": "user", "content": task}],
            },
        )
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise RuntimeError("Pre-Active model response requires exactly one choice")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("Pre-Active model choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Pre-Active model choice has no message")
        if message.get("role") != "assistant":
            raise RuntimeError("Pre-Active model response role is not assistant")
        if (
            "tool_calls" in message
            or "function_call" in message
            or first.get("finish_reason") in ("tool_calls", "function_call")
        ):
            raise RuntimeError("local model attempted a tool call")
        if first.get("finish_reason") != "stop":
            raise RuntimeError("Pre-Active model response did not finish normally")
        return _local_answer_text(message.get("content"))

    raise RuntimeError(f"no resident adapter for provider: {route.provider}")


def invoke_local_messages(route: CognitionRoute, messages: list[dict[str, str]]) -> str:
    """Opt-in role-preserving local chat; never offers the model tools."""
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("local conversation must finish with a user message")
    if any(item.get("role") not in ("user", "assistant") or
           not isinstance(item.get("content"), str) for item in messages):
        raise ValueError("local conversation contains an invalid role or content")
    task = messages[-1]["content"]
    if route.provider == "ollama":
        body = _post_loopback_json(
            port=11434, path="/api/chat", model=route.model_or_agent,
            task=task, timeout_seconds=120.0,
            payload={"model": route.model_or_agent, "messages": messages, "stream": False},
        )
        if body.get("done") is not True:
            raise RuntimeError("Ollama response did not complete")
        message = body.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise RuntimeError("Ollama chat did not return an assistant message")
        if "tool_calls" in message or "function_call" in message:
            raise RuntimeError("local model attempted a tool call")
        return _local_answer_text(message.get("content"))
    if route.provider == "pre_active_local":
        body = _post_loopback_json(
            port=18081, path="/v1/chat/completions", model=route.model_or_agent,
            task=task, timeout_seconds=180.0,
            payload={"model": route.model_or_agent, "messages": messages},
        )
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise RuntimeError("Pre-Active model response requires exactly one choice")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("Pre-Active model choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise RuntimeError("Pre-Active model response role is not assistant")
        if "tool_calls" in message or "function_call" in message or first.get("finish_reason") != "stop":
            raise RuntimeError("Pre-Active model did not complete a tool-free text response")
        return _local_answer_text(message.get("content"))
    raise RuntimeError(f"no local chat adapter for provider: {route.provider}")


# Compatibility alias for callers that explicitly import the old helper.
invoke_ollama_text = invoke_local_text


class ResidentCognitionEngine:
    def __init__(
        self,
        *,
        ledger: CognitionLedger,
        discover_routes: RouteDiscovery = discover_cognition_routes,
        invoke_route: RouteInvoker = invoke_local_text,
        invoke_chat: ChatInvoker = invoke_local_messages,
        authorized_route_ids: frozenset[str] = frozenset(),
        accept_result: Callable[[CognitionRequestEnvelope, CognitionRoute, str], dict[str, object]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.discover_routes = discover_routes
        self.invoke_route = invoke_route
        self.invoke_chat = invoke_chat
        self.authorized_route_ids = authorized_route_ids
        self.accept_result = accept_result
        # Resident process memory only. Never hydrate from the durable cognition
        # ledger: that ledger also contains unrelated autonomous tasks.
        self._context: OrderedDict[str, tuple[str, list[tuple[str, str]]]] = OrderedDict()

    def _chat_messages(
        self, session: str, route: CognitionRoute, task: str,
    ) -> list[dict[str, str]]:
        prior = self._context.get(session)
        pairs = list(prior[1]) if prior is not None and prior[0] == route.route_id else []
        while True:
            messages = [message for user, assistant in pairs for message in (
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            )] + [{"role": "user", "content": task}]
            # Maintain the actual JSON envelope limit including escaping; a
            # malformed/oversized current message fails as an ordinary request.
            body = ({"model": route.model_or_agent, "messages": messages,
                     "stream": False} if route.provider == "ollama" else
                    {"model": route.model_or_agent, "messages": messages})
            if len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= _MAX_LOCAL_PROMPT_BYTES + 1024 or not pairs:
                return messages
            pairs.pop(0)

    def _remember(self, session: str, route: CognitionRoute, task: str, reply: str) -> None:
        prior = self._context.get(session)
        pairs = list(prior[1]) if prior is not None and prior[0] == route.route_id else []
        pairs.append((task, reply))
        while pairs and (len(pairs) > _MAX_CONTEXT_PAIRS or
                         sum(len(user.encode("utf-8")) + len(assistant.encode("utf-8"))
                             for user, assistant in pairs) > _MAX_CONTEXT_BYTES):
            pairs.pop(0)
        self._context[session] = (route.route_id, pairs)
        self._context.move_to_end(session)
        while len(self._context) > _MAX_CONTEXT_SESSIONS:
            self._context.popitem(last=False)

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
        conversation_session_id: str | None = None,
    ) -> CognitionResult:
        if conversation_session_id is not None:
            if request.source != "HUMAN":
                raise ValueError("conversation context is HUMAN-only")
            if not isinstance(conversation_session_id, str) or len(conversation_session_id) != 32 or any(
                char not in "0123456789abcdef" for char in conversation_session_id
            ):
                raise ValueError("invalid conversation session identifier")
        # Durable *opaque* binding protects idempotent request replay across
        # sessions and host restarts, without storing the raw session ID or
        # any earlier conversation turns in SQLite.
        session_binding = (
            hashlib.sha256(("PORTAL_CONVERSATION_SESSION_V1:" + conversation_session_id).encode("ascii")).hexdigest()
            if conversation_session_id is not None else None
        )
        observed_now = float(time.time() if now is None else now)
        attempt_token, stored = self.ledger.claim(
            request, now=observed_now, conversation_binding_digest=session_binding,
        )
        if stored is not None:
            if stored["state"] == "RUNNING":
                return CognitionResult(
                    request_id=request.request_id, state="RUNNING",
                    route_id=str(stored["route_id"]) if stored["route_id"] is not None else None,
                    response_text=None, retryable=bool(stored["retryable"]),
                    evidence_id=str(stored["evidence_id"]),
                    error=("acceptance_outcome_unknown" if stored["acceptance_claimed"]
                           else "request_already_running"),
                )
            return self._result_from_stored(stored)
        assert attempt_token is not None

        try:
            routes = self.discover_routes()
            route = select_cognition_route(
                CognitionRequest(
                    required_capabilities=request.required_capabilities,
                ),
                routes,
                authorized_route_ids=self.authorized_route_ids,
            )
        except Exception as exc:
            observed_now = float(time.time() if now is None else now)
            error = f"{type(exc).__name__}: {exc}"
            evidence_id = self.ledger.finish(
                request, attempt_token=attempt_token, state="FAILED",
                retryable=True, now=observed_now, error=error,
                completed_at=observed_now, conversation_binding_digest=session_binding,
            )
            return CognitionResult(
                request_id=request.request_id, state="FAILED", route_id=None,
                response_text=None, retryable=True, evidence_id=evidence_id, error=error,
            )
        if route is None:
            evidence_id = self.ledger.finish(
                request,
                attempt_token=attempt_token,
                state="UNRESOLVED",
                retryable=True,
                now=observed_now,
                error="no_admissible_cognition_route",
                completed_at=observed_now,
                conversation_binding_digest=session_binding,
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

        try:
            response_text = (
                self.invoke_chat(route, self._chat_messages(conversation_session_id, route, request.task))
                if conversation_session_id is not None else self.invoke_route(route, request.task)
            )
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("cognition response must be nonempty text")
            try:
                response_text.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("cognition response must be UTF-8 text") from exc
        except Exception as exc:
            observed_now = float(time.time() if now is None else now)
            error = f"{type(exc).__name__}: {exc}"
            evidence_id = self.ledger.finish(
                request,
                attempt_token=attempt_token,
                state="FAILED",
                retryable=True,
                now=observed_now,
                route=route,
                error=error,
                completed_at=observed_now,
                conversation_binding_digest=session_binding,
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

        # Atomically reserve the one external acceptance callback. If a former
        # owner was explicitly reconciled while its model was still executing,
        # this CAS fails *before* the callback is run.
        self.ledger.begin_acceptance(request.request_id, attempt_token)
        try:
            acceptance = (
                self.accept_result(request, route, response_text)
                if self.accept_result else {"status": "UNQUALIFIED_LEDGER_ONLY"}
            )
            if not isinstance(acceptance, dict):
                raise ValueError("cognition acceptance must be a JSON object")
            json.dumps(acceptance, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            observed_now = float(time.time() if now is None else now)
            error = f"{type(exc).__name__}: {exc}"
            evidence_id = self.ledger.finish(
                request, attempt_token=attempt_token, state="FAILED",
                retryable=False, now=observed_now, route=route, error=error,
                completed_at=observed_now,
                conversation_binding_digest=session_binding,
                acceptance_claimed=True,
            )
            return CognitionResult(
                request_id=request.request_id, state="FAILED", route_id=route.route_id,
                response_text=None, retryable=False, evidence_id=evidence_id, error=error,
            )

        observed_now = float(time.time() if now is None else now)
        evidence_id = self.ledger.finish(
            request,
            attempt_token=attempt_token,
            state="COMPLETED",
            retryable=False,
            now=observed_now,
            route=route,
            response_text=response_text,
            completed_at=observed_now,
            conversation_binding_digest=session_binding,
            acceptance=acceptance,
            acceptance_claimed=True,
        )
        if conversation_session_id is not None:
            self._remember(conversation_session_id, route, request.task, response_text)
        return CognitionResult(
            request_id=request.request_id,
            state="COMPLETED",
            route_id=route.route_id,
            response_text=response_text,
            retryable=False,
            evidence_id=evidence_id,
        )
