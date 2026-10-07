from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Protocol
import uuid

from .desktop_runtime import CognitionRequestEnvelope, CognitionResult


class _StoreLike(Protocol):
    connection: Any

    def append_journal(
        self,
        *,
        event_type: str,
        subject_id: str,
        payload: dict[str, object],
        now: float,
    ) -> None:
        ...

    def ack_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: float,
    ) -> None:
        ...

    def fail_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        lease_token: str,
        now: float,
        retry_at: float | None = None,
        max_attempts: int | None = None,
        error: str | None = None,
        failed_run_id: str | None = None,
    ) -> bool:
        ...


class _CognitionEngineLike(Protocol):
    def process(
        self,
        request: CognitionRequestEnvelope,
        *,
        now: float | None = None,
    ) -> CognitionResult:
        ...


class _VolitionBridgeLike(Protocol):
    def process_signal_event(
        self,
        *,
        source_event_id: str,
        payload: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ClaimedEvent:
    event_id: str
    kind: str
    payload: dict[str, object]
    priority: int
    attempts: int
    worker_id: str
    lease_until: float
    lease_token: str


def claim_ready_event_kind(
    store: _StoreLike,
    *,
    kind: str,
    worker_id: str,
    now: float,
    lease_seconds: float,
) -> ClaimedEvent | None:
    if not kind.strip():
        raise ValueError("kind is required")
    if not worker_id.strip():
        raise ValueError("worker_id is required")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """
            SELECT *
            FROM events
            WHERE kind=?
              AND available_at <= ?
              AND (
                status='PENDING'
                OR (
                    status='CLAIMED'
                    AND lease_until IS NOT NULL
                    AND lease_until <= ?
                )
              )
            ORDER BY priority DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (kind, now, now),
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None

        attempts = int(row["attempts"]) + 1
        lease_until = now + lease_seconds
        lease_token = str(uuid.uuid4())
        cursor = connection.execute(
            """
            UPDATE events
            SET status='CLAIMED',
                lease_owner=?,
                lease_until=?,
                lease_token=?,
                attempts=?,
                updated_at=?
            WHERE id=?
              AND kind=?
              AND (
                status='PENDING'
                OR (
                    status='CLAIMED'
                    AND lease_until IS NOT NULL
                    AND lease_until <= ?
                )
              )
            """,
            (
                worker_id,
                lease_until,
                lease_token,
                attempts,
                now,
                row["id"],
                kind,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("event claim lost race")
        store.append_journal(
            event_type="EVENT_CLAIMED",
            subject_id=str(row["id"]),
            payload={
                "worker_id": worker_id,
                "attempt": attempts,
                "lease_until": lease_until,
                "kind_filter": kind,
            },
            now=now,
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise

    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    return ClaimedEvent(
        event_id=str(row["id"]),
        kind=str(row["kind"]),
        payload=payload,
        priority=int(row["priority"]),
        attempts=attempts,
        worker_id=worker_id,
        lease_until=lease_until,
        lease_token=lease_token,
    )


def process_one_autonomous_turn(
    store: _StoreLike,
    engine: _CognitionEngineLike,
    *,
    now: float,
    retry_delay_seconds: float = 60.0,
    max_attempts: int = 5,
    worker_id: str = "portal-desktop-autonomous",
    lease_seconds: float = 300.0,
) -> CognitionResult | None:
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be >= 0")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    claim = claim_ready_event_kind(
        store,
        kind="autonomous.turn",
        worker_id=worker_id,
        now=now,
        lease_seconds=lease_seconds,
    )
    if claim is None:
        return None

    try:
        payload = claim.payload
        task = payload.get("task")
        reason = payload.get("reason")
        raw_capabilities = payload.get("capabilities", ["text"])
        if not isinstance(task, str) or not task.strip():
            raise ValueError("autonomous.turn task is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("autonomous.turn reason is required")
        if not isinstance(raw_capabilities, list) or not all(
            isinstance(item, str) and item.strip()
            for item in raw_capabilities
        ):
            raise ValueError(
                "autonomous.turn capabilities must be a list of strings"
            )
        result = engine.process(
            CognitionRequestEnvelope(
                request_id=claim.event_id,
                source="PRE_ACTIVE_AUTONOMOUS_TURN",
                reason=reason.strip(),
                task=task.strip(),
                created_at=now,
                required_capabilities=tuple(
                    item.strip() for item in raw_capabilities
                ),
            ),
            now=now,
        )
    except Exception as exc:
        store.fail_event(
            claim.event_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            now=now,
            retry_at=now + retry_delay_seconds,
            max_attempts=max_attempts,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    if result.state == "COMPLETED":
        store.ack_event(
            claim.event_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            now=now,
        )
        return result

    effective_max_attempts = max_attempts if result.retryable else claim.attempts
    store.fail_event(
        claim.event_id,
        worker_id=claim.worker_id,
        lease_token=claim.lease_token,
        now=now,
        retry_at=now + retry_delay_seconds,
        max_attempts=effective_max_attempts,
        error=result.error or f"cognition state {result.state}",
    )
    return result


def process_one_volition_signal(
    store: _StoreLike,
    *,
    now: float,
    retry_delay_seconds: float = 60.0,
    max_attempts: int = 5,
    worker_id: str = "portal-desktop-volition",
    lease_seconds: float = 300.0,
    bridge_factory: Callable[[_StoreLike], _VolitionBridgeLike] | None = None,
    invalid_signal_types: tuple[type[BaseException], ...] | None = None,
) -> dict[str, Any] | None:
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be >= 0")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    claim = claim_ready_event_kind(
        store,
        kind="volition.signal",
        worker_id=worker_id,
        now=now,
        lease_seconds=lease_seconds,
    )
    if claim is None:
        return None

    if bridge_factory is None or invalid_signal_types is None:
        from pre_active.volition_bridge import (
            InvalidVolitionSignal,
            VolitionBridge,
        )

        if bridge_factory is None:
            bridge_factory = VolitionBridge
        if invalid_signal_types is None:
            invalid_signal_types = (InvalidVolitionSignal,)

    bridge = bridge_factory(store)
    try:
        with store.active_claim_transaction(
            claim.event_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            now=now,
        ) as transition_now:
            receipt = bridge.process_signal_event(
                source_event_id=claim.event_id,
                payload=claim.payload,
                now=transition_now,
            )
            store.ack_event(
                claim.event_id,
                worker_id=claim.worker_id,
                lease_token=claim.lease_token,
                now=transition_now,
            )
        return receipt
    except invalid_signal_types as exc:
        with store.active_claim_transaction(
            claim.event_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            now=now,
        ) as transition_now:
            store.append_journal(
                event_type="EVENT_REJECTED",
                subject_id=claim.event_id,
                payload={"reason": f"invalid volition.signal: {exc}"},
                now=transition_now,
            )
            store.ack_event(
                claim.event_id,
                worker_id=claim.worker_id,
                lease_token=claim.lease_token,
                now=transition_now,
            )
        return {
            "status": "REJECTED",
            "event_id": claim.event_id,
            "reason": str(exc),
            "effect_authority": False,
        }
    except Exception as exc:
        store.fail_event(
            claim.event_id,
            worker_id=claim.worker_id,
            lease_token=claim.lease_token,
            now=now,
            retry_at=now + retry_delay_seconds,
            max_attempts=max_attempts,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
