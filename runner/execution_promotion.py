from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Protocol

from .backends import BackendResult
from .durable_dispatch import SqliteDispatchAdmissionStore
from .github_backend import GitHubTransport
from .leases import Lease
from .portfolio_operator_bridge import _read_live_head
from .recursive_state import _work_from_payload
from .work_units import WorkUnit, WorkUnitStatus, work_unit_fingerprint


NO_PROTECTED_EFFECT = "NO_PROTECTED_EFFECT"
SOURCE_WRITE = "SOURCE_WRITE"
_PROTECTED_EFFECT_CLASSES = frozenset(
    {
        SOURCE_WRITE,
        "MERGE",
        "DEPLOY",
        "INSTALL",
        "CREDENTIAL_OR_PERMISSION_CHANGE",
        "DESTRUCTIVE_EFFECT",
    }
)
_ALLOWED_EFFECTS_BY_CEILING = {
    "NO_EFFECT": frozenset({NO_PROTECTED_EFFECT}),
    "SOURCE_ONLY": frozenset({NO_PROTECTED_EFFECT, SOURCE_WRITE}),
}
_REVIEW_SCHEMA = "PROJECT_RUNNER_EXECUTION_REVIEW_V1"
_EXECUTION_GRANT_SCHEMA = "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1"
_EFFECT_GRANT_SCHEMA = "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_V1"
_PROMOTION_SCHEMA_ID = "PROJECT_RUNNER_EXECUTION_PROMOTION_V1"
_REQUIRED_REVIEW_STATE = "EXECUTION_PROMOTION_REVIEWED"

_PROMOTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_promotions (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    holder TEXT NOT NULL,
    repository TEXT NOT NULL,
    ref TEXT NOT NULL,
    exact_head TEXT NOT NULL,
    operation TEXT NOT NULL,
    effect_class TEXT NOT NULL,
    review_sha256 TEXT NOT NULL,
    review_valid_until REAL NOT NULL,
    execution_grant_sha256 TEXT NOT NULL,
    execution_valid_until REAL NOT NULL,
    effect_grant_sha256 TEXT,
    effect_valid_until REAL,
    promoted_at REAL NOT NULL,
    attempt_work_generation INTEGER NOT NULL,
    promoted_work_generation INTEGER NOT NULL,
    promotion_sha256 TEXT NOT NULL,
    PRIMARY KEY (lineage_id, work_fingerprint, fencing_token),
    FOREIGN KEY (lineage_id, work_fingerprint, fencing_token)
        REFERENCES execution_attempts (
            lineage_id, work_fingerprint, fencing_token
        )
);
"""

_PROMOTION_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_promotion_requests (
    lineage_id TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    PRIMARY KEY (lineage_id, work_fingerprint, fencing_token),
    FOREIGN KEY (lineage_id, work_fingerprint, fencing_token)
        REFERENCES execution_promotions (
            lineage_id, work_fingerprint, fencing_token
        )
);
"""


def _ensure_promotion_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_PROMOTION_SCHEMA)
    connection.executescript(_PROMOTION_REQUEST_SCHEMA)


@dataclass(frozen=True)
class ExecutionReviewEvidence:
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    plan_sha256: str
    work_fingerprint: str
    reviewer_identity: str
    review_gate: str
    review_state: str
    reviewed_at: float
    valid_until: float
    execution_request_sha256: str | None
    sha256: str


@dataclass(frozen=True)
class ExecutionAuthorityGrant:
    grant_id: str
    issuer: str
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    lineage_id: str
    work_fingerprint: str
    fencing_token: int
    operation: str
    effect_class: str
    issued_at: float
    valid_until: float
    execution_request: Mapping[str, object] | None
    execution_request_sha256: str | None
    sha256: str


@dataclass(frozen=True)
class ProtectedEffectAuthorityGrant:
    grant_id: str
    issuer: str
    subject_id: str
    repository: str
    ref: str
    exact_head: str
    lineage_id: str
    work_fingerprint: str
    fencing_token: int
    effect_class: str
    issued_at: float
    valid_until: float
    execution_request_sha256: str | None
    sha256: str


@dataclass(frozen=True)
class ExecutionPromotionReceipt:
    lineage_id: str
    work_fingerprint: str
    fencing_token: int
    holder: str
    repository: str
    ref: str
    exact_head: str
    operation: str
    effect_class: str
    review_sha256: str
    review_valid_until: float
    execution_grant_sha256: str
    execution_valid_until: float
    execution_request_sha256: str | None
    effect_grant_sha256: str | None
    effect_valid_until: float | None
    promoted_at: float
    attempt_work_generation: int
    promoted_work_generation: int
    promotion_sha256: str


@dataclass(frozen=True)
class PromotedExecution:
    work: WorkUnit
    operation: str
    effect_class: str
    execution_request: Mapping[str, object] | None
    promotion: ExecutionPromotionReceipt


class PromotedExecutionBackend(Protocol):
    def execute_promoted(self, execution: PromotedExecution) -> BackendResult: ...


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def sign_evidence(document: Mapping[str, object], key: bytes) -> dict[str, object]:
    """Return an HMAC-bound document for tests/tooling.

    Possession of the key, not this helper, is the authority boundary.
    """
    if not key:
        raise ValueError("evidence signing key is required")
    payload = dict(document)
    payload.pop("hmac_sha256", None)
    signature = hmac.new(key, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    payload["hmac_sha256"] = signature
    return payload


def _verify_signed(
    document: Mapping[str, object],
    *,
    expected_schema: str,
    key: bytes,
    label: str,
) -> tuple[dict[str, object], str]:
    if not key:
        raise ValueError(f"{label} verification key is required")
    payload = dict(document)
    signature = str(payload.pop("hmac_sha256", ""))
    if len(signature) != 64:
        raise ValueError(f"{label} signature is invalid")
    if payload.get("schema") != expected_schema:
        raise ValueError(f"unexpected {label} schema")
    expected = hmac.new(
        key,
        _canonical_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError(f"{label} signature mismatch")
    return payload, _sha256(payload)


def _text(payload: Mapping[str, object], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value


def _number(payload: Mapping[str, object], key: str, label: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _integer(payload: Mapping[str, object], key: str, label: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _optional_digest(
    payload: Mapping[str, object],
    key: str,
    label: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{label} must be lowercase sha256")
    return text


def _execution_request(
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object] | None, str | None]:
    value = payload.get("execution_request")
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        raise ValueError("execution request must be an object")
    request_payload = dict(value)
    return request_payload, _sha256(request_payload)


def parse_review_evidence(
    document: Mapping[str, object],
    *,
    key: bytes,
) -> ExecutionReviewEvidence:
    payload, digest = _verify_signed(
        document,
        expected_schema=_REVIEW_SCHEMA,
        key=key,
        label="review evidence",
    )
    state = _text(payload, "review_state", "review state")
    if state != _REQUIRED_REVIEW_STATE:
        raise ValueError("review evidence is not execution-promotion reviewed")
    return ExecutionReviewEvidence(
        subject_id=_text(payload, "subject_id", "review subject id"),
        repository=_text(payload, "repository", "review repository"),
        ref=_text(payload, "ref", "review ref"),
        exact_head=_text(payload, "exact_head", "review exact head"),
        plan_sha256=_text(payload, "plan_sha256", "review plan sha256"),
        work_fingerprint=_text(
            payload,
            "work_fingerprint",
            "review work fingerprint",
        ),
        reviewer_identity=_text(
            payload,
            "reviewer_identity",
            "reviewer identity",
        ),
        review_gate=_text(payload, "review_gate", "review gate"),
        review_state=state,
        reviewed_at=_number(payload, "reviewed_at", "reviewed_at"),
        valid_until=_number(payload, "valid_until", "review valid_until"),
        execution_request_sha256=_optional_digest(
            payload,
            "execution_request_sha256",
            "review execution request sha256",
        ),
        sha256=digest,
    )


def parse_execution_grant(
    document: Mapping[str, object],
    *,
    key: bytes,
) -> ExecutionAuthorityGrant:
    payload, digest = _verify_signed(
        document,
        expected_schema=_EXECUTION_GRANT_SCHEMA,
        key=key,
        label="execution authority grant",
    )
    if payload.get("execution_authorized") is not True:
        raise ValueError("execution authority grant does not authorize execution")
    execution_request, execution_request_sha256 = _execution_request(payload)
    return ExecutionAuthorityGrant(
        grant_id=_text(payload, "grant_id", "execution grant id"),
        issuer=_text(payload, "issuer", "execution grant issuer"),
        subject_id=_text(payload, "subject_id", "execution subject id"),
        repository=_text(payload, "repository", "execution repository"),
        ref=_text(payload, "ref", "execution ref"),
        exact_head=_text(payload, "exact_head", "execution exact head"),
        lineage_id=_text(payload, "lineage_id", "execution lineage id"),
        work_fingerprint=_text(
            payload,
            "work_fingerprint",
            "execution work fingerprint",
        ),
        fencing_token=_integer(
            payload,
            "fencing_token",
            "execution fencing token",
        ),
        operation=_text(payload, "operation", "execution operation"),
        effect_class=_text(payload, "effect_class", "execution effect class"),
        issued_at=_number(payload, "issued_at", "execution issued_at"),
        valid_until=_number(
            payload,
            "valid_until",
            "execution valid_until",
        ),
        execution_request=execution_request,
        execution_request_sha256=execution_request_sha256,
        sha256=digest,
    )


def parse_effect_grant(
    document: Mapping[str, object],
    *,
    key: bytes,
) -> ProtectedEffectAuthorityGrant:
    payload, digest = _verify_signed(
        document,
        expected_schema=_EFFECT_GRANT_SCHEMA,
        key=key,
        label="protected-effect authority grant",
    )
    if payload.get("protected_effects_authorized") is not True:
        raise ValueError(
            "protected-effect authority grant does not authorize effects"
        )
    return ProtectedEffectAuthorityGrant(
        grant_id=_text(payload, "grant_id", "effect grant id"),
        issuer=_text(payload, "issuer", "effect grant issuer"),
        subject_id=_text(payload, "subject_id", "effect subject id"),
        repository=_text(payload, "repository", "effect repository"),
        ref=_text(payload, "ref", "effect ref"),
        exact_head=_text(payload, "exact_head", "effect exact head"),
        lineage_id=_text(payload, "lineage_id", "effect lineage id"),
        work_fingerprint=_text(
            payload,
            "work_fingerprint",
            "effect work fingerprint",
        ),
        fencing_token=_integer(
            payload,
            "fencing_token",
            "effect fencing token",
        ),
        effect_class=_text(payload, "effect_class", "effect class"),
        issued_at=_number(payload, "issued_at", "effect issued_at"),
        valid_until=_number(payload, "valid_until", "effect valid_until"),
        execution_request_sha256=_optional_digest(
            payload,
            "execution_request_sha256",
            "effect execution request sha256",
        ),
        sha256=digest,
    )


def _assert_fresh(
    *,
    issued_at: float,
    valid_until: float,
    now: float,
    label: str,
) -> None:
    if issued_at > now:
        raise ValueError(f"{label} is not active yet")
    if valid_until <= issued_at:
        raise ValueError(f"{label} validity interval is invalid")
    if now >= valid_until:
        raise ValueError(f"{label} is stale")


def _load_claim_snapshot(
    store: SqliteDispatchAdmissionStore,
    *,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    holder: str,
    now: float,
) -> tuple[WorkUnit, int, float, Mapping[str, object]]:
    row = store.connection.execute(
        """
        SELECT work_json, status, generation
        FROM recursive_work_state
        WHERE lineage_id = ? AND work_fingerprint = ?
        """,
        (lineage_id, work_fingerprint_value),
    ).fetchone()
    if row is None:
        raise ValueError("promotion durable work state not found")
    try:
        work_payload = json.loads(str(row[0]))
        status = WorkUnitStatus(str(row[1]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("promotion durable work state is invalid") from exc
    if status is not WorkUnitStatus.CLAIMED:
        raise ValueError("execution promotion requires CLAIMED work")
    work = _work_from_payload(work_payload, status=status)
    if work_unit_fingerprint(work) != work_fingerprint_value:
        raise ValueError("promotion work semantic identity mismatch")
    claim = work.payload
    if claim.get("schema") != "PROJECT_RUNNER_BOUND_PLAN_CLAIM_V1":
        raise ValueError("execution promotion requires bound-plan claim work")
    if claim.get("execution_authority") is not False:
        raise ValueError("claim work unexpectedly carries execution authority")
    if claim.get("protected_effects_authorized") is not False:
        raise ValueError(
            "claim work unexpectedly carries protected-effect authority"
        )

    lease = store.connection.execute(
        """
        SELECT holder, fencing_token, expires_at, completed
        FROM leases WHERE work_fingerprint = ?
        """,
        (work_fingerprint_value,),
    ).fetchone()
    if lease is None:
        raise ValueError("execution promotion lease is missing")
    lease_holder, current_token, expires_at, completed = lease
    if bool(completed):
        raise ValueError("execution promotion lease is completed")
    if int(current_token) != fencing_token:
        raise ValueError("execution promotion fencing token is stale")
    if str(lease_holder) != holder:
        raise ValueError("execution promotion lease holder mismatch")
    if now >= float(expires_at):
        raise ValueError("execution promotion lease is expired")

    attempt = store._attempt_row(
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
    )
    work_generation = int(row[2])
    if attempt[0] != holder:
        raise ValueError("promotion attempt holder mismatch")
    if attempt[3] != work_generation:
        raise ValueError("promotion attempt/work generation mismatch")
    if attempt[5] is not None or attempt[6] is not None:
        raise ValueError("execution promotion cannot follow backend result")
    if store.connection.execute(
        """
        SELECT 1 FROM execution_verifications
        WHERE lineage_id = ? AND work_fingerprint = ?
          AND fencing_token = ?
        LIMIT 1
        """,
        (lineage_id, work_fingerprint_value, fencing_token),
    ).fetchone() is not None:
        raise ValueError("execution promotion cannot follow verification history")
    if store.connection.execute(
        """
        SELECT 1 FROM execution_reconciliations
        WHERE lineage_id = ? AND work_fingerprint = ?
          AND fencing_token = ?
        LIMIT 1
        """,
        (lineage_id, work_fingerprint_value, fencing_token),
    ).fetchone() is not None:
        raise ValueError(
            "execution promotion cannot follow reconciliation history"
        )
    return work, work_generation, float(expires_at), claim


def _validate_bindings(
    *,
    claim: Mapping[str, object],
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    review: ExecutionReviewEvidence,
    execution: ExecutionAuthorityGrant,
    effect: ProtectedEffectAuthorityGrant | None,
    now: float,
) -> None:
    repository = _text(claim, "repository", "claim repository")
    ref = _text(claim, "ref", "claim ref")
    exact_head = _text(claim, "exact_head", "claim exact head")
    subject_id = _text(claim, "subject_id", "claim subject id")
    plan_sha256 = _text(claim, "plan_sha256", "claim plan sha256")
    selected = claim.get("selected")
    if not isinstance(selected, Mapping):
        raise ValueError("claim selected metadata is missing")
    reviewer_identities = selected.get("reviewer_identities")
    if not isinstance(reviewer_identities, list) or any(
        not isinstance(item, str) or not item
        for item in reviewer_identities
    ):
        raise ValueError("claim reviewer identities are invalid")
    review_gate = _text(selected, "review_gate", "claim review gate")
    action = _text(selected, "action", "claim action")
    if execution.operation != action:
        raise ValueError("execution operation does not match claim action")
    effect_ceiling = _text(selected, "effect_ceiling", "claim effect ceiling")
    allowed_effects = _ALLOWED_EFFECTS_BY_CEILING.get(effect_ceiling)
    if allowed_effects is None:
        raise ValueError("claim effect ceiling is unsupported")
    if execution.effect_class not in allowed_effects:
        raise ValueError("execution effect class exceeds claim effect ceiling")
    request_sha256 = execution.execution_request_sha256
    if execution.effect_class == SOURCE_WRITE and request_sha256 is None:
        raise ValueError("source-write execution requires an exact execution request")
    if review.execution_request_sha256 != request_sha256:
        raise ValueError("review evidence does not bind exact execution request")

    common = (
        review.subject_id == subject_id
        and review.repository == repository
        and review.ref == ref
        and review.exact_head == exact_head
        and review.plan_sha256 == plan_sha256
        and review.work_fingerprint == work_fingerprint_value
    )
    if not common:
        raise ValueError("review evidence does not bind exact claim")
    if review.reviewer_identity not in reviewer_identities:
        raise ValueError("reviewer identity is not required by the claim")
    if review.review_gate != review_gate:
        raise ValueError("review evidence gate does not match claim")
    _assert_fresh(
        issued_at=review.reviewed_at,
        valid_until=review.valid_until,
        now=now,
        label="review evidence",
    )

    if (
        execution.subject_id != subject_id
        or execution.repository != repository
        or execution.ref != ref
        or execution.exact_head != exact_head
        or execution.lineage_id != lineage_id
        or execution.work_fingerprint != work_fingerprint_value
        or execution.fencing_token != fencing_token
    ):
        raise ValueError("execution authority does not bind exact claim/fence")
    _assert_fresh(
        issued_at=execution.issued_at,
        valid_until=execution.valid_until,
        now=now,
        label="execution authority",
    )

    if execution.effect_class == NO_PROTECTED_EFFECT:
        if effect is not None:
            raise ValueError(
                "protected-effect grant supplied for no-protected-effect execution"
            )
        return

    if effect is None:
        raise ValueError(
            "protected-effect authority is required separately from execution authority"
        )
    if (
        effect.subject_id != subject_id
        or effect.repository != repository
        or effect.ref != ref
        or effect.exact_head != exact_head
        or effect.lineage_id != lineage_id
        or effect.work_fingerprint != work_fingerprint_value
        or effect.fencing_token != fencing_token
        or effect.effect_class != execution.effect_class
        or effect.execution_request_sha256 != request_sha256
    ):
        raise ValueError("protected-effect authority does not bind exact claim/fence")
    _assert_fresh(
        issued_at=effect.issued_at,
        valid_until=effect.valid_until,
        now=now,
        label="protected-effect authority",
    )


def _promotion_payload(
    *,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    holder: str,
    repository: str,
    ref: str,
    exact_head: str,
    operation: str,
    effect_class: str,
    review: ExecutionReviewEvidence,
    execution: ExecutionAuthorityGrant,
    effect: ProtectedEffectAuthorityGrant | None,
    promoted_at: float,
    attempt_work_generation: int,
    promoted_work_generation: int,
) -> dict[str, object]:
    return {
        "schema": _PROMOTION_SCHEMA_ID,
        "lineage_id": lineage_id,
        "work_fingerprint": work_fingerprint_value,
        "fencing_token": fencing_token,
        "holder": holder,
        "repository": repository,
        "ref": ref,
        "exact_head": exact_head,
        "operation": operation,
        "effect_class": effect_class,
        "review_sha256": review.sha256,
        "review_valid_until": review.valid_until,
        "execution_grant_sha256": execution.sha256,
        "execution_valid_until": execution.valid_until,
        "execution_request_sha256": execution.execution_request_sha256,
        "effect_grant_sha256": effect.sha256 if effect is not None else None,
        "effect_valid_until": effect.valid_until if effect is not None else None,
        "promoted_at": promoted_at,
        "attempt_work_generation": attempt_work_generation,
        "promoted_work_generation": promoted_work_generation,
    }


def promote_claimed_to_running(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
    holder: str,
    review_document: Mapping[str, object],
    execution_grant_document: Mapping[str, object],
    effect_grant_document: Mapping[str, object] | None,
    review_key: bytes,
    execution_authority_key: bytes,
    effect_authority_key: bytes | None,
    token: str | None = None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> ExecutionPromotionReceipt:
    review = parse_review_evidence(review_document, key=review_key)
    execution = parse_execution_grant(
        execution_grant_document,
        key=execution_authority_key,
    )
    effect = None
    if effect_grant_document is not None:
        if not effect_authority_key:
            raise ValueError("protected-effect authority verification key is required")
        effect = parse_effect_grant(
            effect_grant_document,
            key=effect_authority_key,
        )

    store = SqliteDispatchAdmissionStore(Path(state_db))
    _ensure_promotion_schema(store.connection)
    try:
        precheck_at = float(clock())
        status_row = store.connection.execute(
            """
            SELECT status
            FROM recursive_work_state
            WHERE lineage_id = ? AND work_fingerprint = ?
            """,
            (lineage_id, work_fingerprint_value),
        ).fetchone()
        if (
            status_row is not None
            and WorkUnitStatus(str(status_row[0])) is WorkUnitStatus.RUNNING
        ):
            existing = _read_durable_promotion(
                store,
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
            )
            work, lease_expires_at, _request = _load_promotion(store, existing)
            claim = work.payload
            _validate_bindings(
                claim=claim,
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
                review=review,
                execution=execution,
                effect=effect,
                now=precheck_at,
            )
            if existing.holder != holder:
                raise ValueError("execution promotion replay holder mismatch")
            if existing.review_sha256 != review.sha256:
                raise ValueError("execution promotion replay review mismatch")
            if existing.execution_grant_sha256 != execution.sha256:
                raise ValueError("execution promotion replay authority mismatch")
            expected_effect_sha = effect.sha256 if effect is not None else None
            if existing.effect_grant_sha256 != expected_effect_sha:
                raise ValueError("execution promotion replay effect authority mismatch")
            if existing.operation != execution.operation:
                raise ValueError("execution promotion replay operation mismatch")
            if existing.effect_class != execution.effect_class:
                raise ValueError("execution promotion replay effect class mismatch")
            if precheck_at >= lease_expires_at:
                raise ValueError("execution promotion replay lease is expired")
            observed_head = _read_live_head(
                repository=existing.repository,
                ref=existing.ref,
                token=token,
                transport=transport,
            )
            if observed_head != existing.exact_head:
                raise ValueError("execution promotion replay source head is stale")
            return existing

        work, work_generation, _lease_expiry, claim = _load_claim_snapshot(
            store,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            holder=holder,
            now=precheck_at,
        )
        _validate_bindings(
            claim=claim,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            review=review,
            execution=execution,
            effect=effect,
            now=precheck_at,
        )
        repository = _text(claim, "repository", "claim repository")
        ref = _text(claim, "ref", "claim ref")
        exact_head = _text(claim, "exact_head", "claim exact head")

        observed_head = _read_live_head(
            repository=repository,
            ref=ref,
            token=token,
            transport=transport,
        )
        if observed_head != exact_head:
            raise ValueError("execution promotion source head is stale")

        promoted_at = float(clock())
        _validate_bindings(
            claim=claim,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            review=review,
            execution=execution,
            effect=effect,
            now=promoted_at,
        )

        store.connection.execute("BEGIN IMMEDIATE")
        current_work, current_generation, _expiry, current_claim = (
            _load_claim_snapshot(
                store,
                lineage_id=lineage_id,
                work_fingerprint_value=work_fingerprint_value,
                fencing_token=fencing_token,
                holder=holder,
                now=promoted_at,
            )
        )
        if current_generation != work_generation:
            raise ValueError("execution promotion work generation changed")
        if work_unit_fingerprint(current_work) != work_unit_fingerprint(work):
            raise ValueError("execution promotion work changed")
        if dict(current_claim) != dict(claim):
            raise ValueError("execution promotion claim payload changed")

        promoted_work_generation = work_generation + 1
        payload = _promotion_payload(
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
            holder=holder,
            repository=repository,
            ref=ref,
            exact_head=exact_head,
            operation=execution.operation,
            effect_class=execution.effect_class,
            review=review,
            execution=execution,
            effect=effect,
            promoted_at=promoted_at,
            attempt_work_generation=work_generation,
            promoted_work_generation=promoted_work_generation,
        )
        promotion_sha256 = _sha256(payload)
        request_json = (
            _canonical_bytes(execution.execution_request).decode("utf-8")
            if execution.execution_request is not None
            else None
        )
        store.connection.execute(
            """
            INSERT INTO execution_promotions (
                lineage_id, work_fingerprint, fencing_token, holder,
                repository, ref, exact_head, operation, effect_class,
                review_sha256, review_valid_until,
                execution_grant_sha256, execution_valid_until,
                effect_grant_sha256, effect_valid_until,
                promoted_at, attempt_work_generation,
                promoted_work_generation, promotion_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lineage_id,
                work_fingerprint_value,
                fencing_token,
                holder,
                repository,
                ref,
                exact_head,
                execution.operation,
                execution.effect_class,
                review.sha256,
                review.valid_until,
                execution.sha256,
                execution.valid_until,
                effect.sha256 if effect is not None else None,
                effect.valid_until if effect is not None else None,
                promoted_at,
                work_generation,
                promoted_work_generation,
                promotion_sha256,
            ),
        )
        if execution.execution_request is not None:
            assert request_json is not None
            assert execution.execution_request_sha256 is not None
            store.connection.execute(
                """
                INSERT INTO execution_promotion_requests (
                    lineage_id, work_fingerprint, fencing_token,
                    request_json, request_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    lineage_id,
                    work_fingerprint_value,
                    fencing_token,
                    request_json,
                    execution.execution_request_sha256,
                ),
            )
        updated = store.connection.execute(
            """
            UPDATE recursive_work_state
            SET status = ?, generation = generation + 1
            WHERE lineage_id = ? AND work_fingerprint = ?
              AND status = ? AND generation = ?
            """,
            (
                WorkUnitStatus.RUNNING.value,
                lineage_id,
                work_fingerprint_value,
                WorkUnitStatus.CLAIMED.value,
                work_generation,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("execution promotion work changed during commit")
        store.connection.commit()
    except BaseException:
        if store.connection.in_transaction:
            store.connection.rollback()
        raise
    finally:
        store.close()

    return ExecutionPromotionReceipt(
        lineage_id=lineage_id,
        work_fingerprint=work_fingerprint_value,
        fencing_token=fencing_token,
        holder=holder,
        repository=repository,
        ref=ref,
        exact_head=exact_head,
        operation=execution.operation,
        effect_class=execution.effect_class,
        review_sha256=review.sha256,
        review_valid_until=review.valid_until,
        execution_grant_sha256=execution.sha256,
        execution_valid_until=execution.valid_until,
        execution_request_sha256=execution.execution_request_sha256,
        effect_grant_sha256=effect.sha256 if effect is not None else None,
        effect_valid_until=effect.valid_until if effect is not None else None,
        promoted_at=promoted_at,
        attempt_work_generation=work_generation,
        promoted_work_generation=promoted_work_generation,
        promotion_sha256=promotion_sha256,
    )


def _read_execution_request(
    store: SqliteDispatchAdmissionStore,
    *,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
) -> tuple[Mapping[str, object] | None, str | None]:
    _ensure_promotion_schema(store.connection)
    row = store.connection.execute(
        """
        SELECT request_json, request_sha256
        FROM execution_promotion_requests
        WHERE lineage_id = ? AND work_fingerprint = ? AND fencing_token = ?
        """,
        (lineage_id, work_fingerprint_value, fencing_token),
    ).fetchone()
    if row is None:
        return None, None
    try:
        payload = json.loads(str(row[0]))
    except json.JSONDecodeError as exc:
        raise ValueError("durable execution request is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("durable execution request must be an object")
    digest = _sha256(payload)
    if not hmac.compare_digest(str(row[1]), digest):
        raise ValueError("durable execution request digest mismatch")
    return dict(payload), digest


def _read_durable_promotion(
    store: SqliteDispatchAdmissionStore,
    *,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
) -> ExecutionPromotionReceipt:
    _ensure_promotion_schema(store.connection)
    row = store.connection.execute(
        """
        SELECT holder, repository, ref, exact_head, operation, effect_class,
               review_sha256, review_valid_until,
               execution_grant_sha256, execution_valid_until,
               effect_grant_sha256, effect_valid_until,
               promoted_at, attempt_work_generation,
               promoted_work_generation, promotion_sha256
        FROM execution_promotions
        WHERE lineage_id = ? AND work_fingerprint = ? AND fencing_token = ?
        """,
        (lineage_id, work_fingerprint_value, fencing_token),
    ).fetchone()
    if row is None:
        raise ValueError("execution promotion receipt is not durable")
    _request, request_sha256 = _read_execution_request(
        store,
        lineage_id=lineage_id,
        work_fingerprint_value=work_fingerprint_value,
        fencing_token=fencing_token,
    )

    payload = {
        "schema": _PROMOTION_SCHEMA_ID,
        "lineage_id": lineage_id,
        "work_fingerprint": work_fingerprint_value,
        "fencing_token": fencing_token,
        "holder": str(row[0]),
        "repository": str(row[1]),
        "ref": str(row[2]),
        "exact_head": str(row[3]),
        "operation": str(row[4]),
        "effect_class": str(row[5]),
        "review_sha256": str(row[6]),
        "review_valid_until": float(row[7]),
        "execution_grant_sha256": str(row[8]),
        "execution_valid_until": float(row[9]),
        "execution_request_sha256": request_sha256,
        "effect_grant_sha256": str(row[10]) if row[10] is not None else None,
        "effect_valid_until": float(row[11]) if row[11] is not None else None,
        "promoted_at": float(row[12]),
        "attempt_work_generation": int(row[13]),
        "promoted_work_generation": int(row[14]),
    }
    digest = _sha256(payload)
    stored_digest = str(row[15])
    if not hmac.compare_digest(stored_digest, digest):
        if request_sha256 is not None:
            raise ValueError("execution promotion digest mismatch")
        legacy_payload = dict(payload)
        legacy_payload.pop("execution_request_sha256", None)
        legacy_digest = _sha256(legacy_payload)
        if not hmac.compare_digest(stored_digest, legacy_digest):
            raise ValueError("execution promotion digest mismatch")
        digest = legacy_digest
    return ExecutionPromotionReceipt(
        lineage_id=lineage_id,
        work_fingerprint=work_fingerprint_value,
        fencing_token=fencing_token,
        holder=str(row[0]),
        repository=str(row[1]),
        ref=str(row[2]),
        exact_head=str(row[3]),
        operation=str(row[4]),
        effect_class=str(row[5]),
        review_sha256=str(row[6]),
        review_valid_until=float(row[7]),
        execution_grant_sha256=str(row[8]),
        execution_valid_until=float(row[9]),
        execution_request_sha256=request_sha256,
        effect_grant_sha256=str(row[10]) if row[10] is not None else None,
        effect_valid_until=float(row[11]) if row[11] is not None else None,
        promoted_at=float(row[12]),
        attempt_work_generation=int(row[13]),
        promoted_work_generation=int(row[14]),
        promotion_sha256=digest,
    )


def _load_promotion(
    store: SqliteDispatchAdmissionStore,
    receipt: ExecutionPromotionReceipt,
) -> tuple[WorkUnit, float, Mapping[str, object] | None]:
    durable_receipt = _read_durable_promotion(
        store,
        lineage_id=receipt.lineage_id,
        work_fingerprint_value=receipt.work_fingerprint,
        fencing_token=receipt.fencing_token,
    )
    if receipt != durable_receipt:
        raise ValueError("execution promotion receipt does not match durable state")

    work_row = store.connection.execute(
        """
        SELECT work_json, status, generation
        FROM recursive_work_state
        WHERE lineage_id = ? AND work_fingerprint = ?
        """,
        (receipt.lineage_id, receipt.work_fingerprint),
    ).fetchone()
    if work_row is None:
        raise ValueError("promoted work state not found")
    if WorkUnitStatus(str(work_row[1])) is not WorkUnitStatus.RUNNING:
        raise ValueError("backend execution requires RUNNING work")
    if int(work_row[2]) != receipt.promoted_work_generation:
        raise ValueError("promotion/work generation mismatch")
    work = _work_from_payload(
        json.loads(str(work_row[0])),
        status=WorkUnitStatus.RUNNING,
    )
    if work_unit_fingerprint(work) != receipt.work_fingerprint:
        raise ValueError("promoted work semantic identity mismatch")

    lease = store.connection.execute(
        """
        SELECT holder, fencing_token, expires_at, completed
        FROM leases WHERE work_fingerprint = ?
        """,
        (receipt.work_fingerprint,),
    ).fetchone()
    if lease is None:
        raise ValueError("promoted execution lease is missing")
    if bool(lease[3]):
        raise ValueError("promoted execution lease is completed")
    if int(lease[1]) != receipt.fencing_token:
        raise ValueError("promoted execution fence is stale")
    if str(lease[0]) != receipt.holder:
        raise ValueError("promoted execution lease holder mismatch")

    attempt = store._attempt_row(
        lineage_id=receipt.lineage_id,
        work_fingerprint_value=receipt.work_fingerprint,
        fencing_token=receipt.fencing_token,
    )
    if attempt[0] != receipt.holder:
        raise ValueError("promoted execution attempt holder mismatch")
    if attempt[3] != receipt.attempt_work_generation:
        raise ValueError("promoted execution attempt generation mismatch")
    if attempt[5] is not None or attempt[6] is not None:
        raise ValueError("promoted execution already has a backend result")

    execution_request, request_sha256 = _read_execution_request(
        store,
        lineage_id=receipt.lineage_id,
        work_fingerprint_value=receipt.work_fingerprint,
        fencing_token=receipt.fencing_token,
    )
    if receipt.execution_request_sha256 != request_sha256:
        raise ValueError("promotion/request binding mismatch")
    return work, float(lease[2]), execution_request


def load_durable_promotion_receipt(
    *,
    state_db: Path,
    lineage_id: str,
    work_fingerprint_value: str,
    fencing_token: int,
) -> ExecutionPromotionReceipt:
    """Recover and digest-verify an existing authorized promotion."""
    store = SqliteDispatchAdmissionStore(Path(state_db))
    try:
        return _read_durable_promotion(
            store,
            lineage_id=lineage_id,
            work_fingerprint_value=work_fingerprint_value,
            fencing_token=fencing_token,
        )
    finally:
        store.close()


def execute_promoted(
    *,
    state_db: Path,
    receipt: ExecutionPromotionReceipt,
    backend: PromotedExecutionBackend,
    token: str | None = None,
    transport: GitHubTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> BackendResult:
    store = SqliteDispatchAdmissionStore(Path(state_db))
    try:
        now = float(clock())
        work, lease_expires_at, execution_request = _load_promotion(store, receipt)
        if now >= lease_expires_at:
            raise ValueError("promoted execution lease is expired")
        if now >= receipt.review_valid_until:
            raise ValueError("promoted execution review is stale")
        if now >= receipt.execution_valid_until:
            raise ValueError("promoted execution authority is stale")
        if (
            receipt.effect_class != NO_PROTECTED_EFFECT
            and (
                receipt.effect_valid_until is None
                or now >= receipt.effect_valid_until
            )
        ):
            raise ValueError("promoted protected-effect authority is stale")

        observed = _read_live_head(
            repository=receipt.repository,
            ref=receipt.ref,
            token=token,
            transport=transport,
        )
        if observed != receipt.exact_head:
            raise ValueError("promoted execution source head is stale")

        execute_at = float(clock())
        work, lease_expires_at, execution_request = _load_promotion(store, receipt)
        if execute_at >= lease_expires_at:
            raise ValueError("promoted execution lease expired before backend call")
        if execute_at >= receipt.review_valid_until:
            raise ValueError("promoted execution review expired before backend call")
        if execute_at >= receipt.execution_valid_until:
            raise ValueError(
                "promoted execution authority expired before backend call"
            )
        if (
            receipt.effect_class != NO_PROTECTED_EFFECT
            and (
                receipt.effect_valid_until is None
                or execute_at >= receipt.effect_valid_until
            )
        ):
            raise ValueError(
                "protected-effect authority expired before backend call"
            )

        execution = PromotedExecution(
            work=work,
            operation=receipt.operation,
            effect_class=receipt.effect_class,
            execution_request=execution_request,
            promotion=receipt,
        )
        result = backend.execute_promoted(execution)
        if result.work_fingerprint != receipt.work_fingerprint:
            raise ValueError("promoted backend result fingerprint mismatch")
        store.record_result(
            lineage_id=receipt.lineage_id,
            work_fingerprint_value=receipt.work_fingerprint,
            fencing_token=receipt.fencing_token,
            result=result,
            recorded_at=execute_at,
        )
        return result
    finally:
        store.close()


def load_json_document(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load promotion evidence: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("promotion evidence must be a JSON object")
    return value


def review_key_from_environment() -> bytes:
    value = os.environ.get("PROJECT_RUNNER_REVIEW_EVIDENCE_KEY", "")
    if not value:
        raise ValueError("PROJECT_RUNNER_REVIEW_EVIDENCE_KEY is required")
    return value.encode("utf-8")


def execution_authority_key_from_environment() -> bytes:
    value = os.environ.get("PROJECT_RUNNER_EXECUTION_AUTHORITY_KEY", "")
    if not value:
        raise ValueError("PROJECT_RUNNER_EXECUTION_AUTHORITY_KEY is required")
    return value.encode("utf-8")


def effect_authority_key_from_environment() -> bytes | None:
    value = os.environ.get("PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_KEY", "")
    return value.encode("utf-8") if value else None
