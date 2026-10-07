"""Desktop authority decisions bound to Project Runner's existing grant protocol."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Mapping

from runner.execution_promotion import (
    NO_PROTECTED_EFFECT,
    effect_authority_key_from_environment,
    execution_authority_key_from_environment,
    parse_effect_grant,
    parse_execution_grant,
    sign_evidence,
)


_REQUEST_SCHEMA = "PORTAL_DESKTOP_AUTHORITY_REQUEST_V1"
_DECISION_SCHEMA = "PORTAL_DESKTOP_AUTHORITY_DECISION_V1"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_EFFECTS_BY_CEILING = {
    "NO_EFFECT": frozenset({NO_PROTECTED_EFFECT}),
    "SOURCE_ONLY": frozenset({NO_PROTECTED_EFFECT, "SOURCE_WRITE"}),
}


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


def _required_text(
    payload: Mapping[str, object],
    key: str,
    label: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _required_int(
    payload: Mapping[str, object],
    key: str,
    label: str,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _required_number(
    payload: Mapping[str, object],
    key: str,
    label: str,
) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"authority request is not valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError("authority request must be an object")
    return value


class DesktopAuthorityService:
    """Mint exact Project Runner grants from explicit desktop user decisions.

    This service does not execute or promote work. It creates the authority
    documents that Project Runner already knows how to verify.
    """

    def __init__(
        self,
        runtime_root: Path,
        *,
        execution_key: bytes | None = None,
        effect_key: bytes | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.inbox = (
            self.runtime_root / "state" / "portal" / "authority-requests"
        )
        self.grants_root = (
            self.runtime_root / "state" / "portal" / "authority-grants"
        )
        self.decisions_root = (
            self.runtime_root / "state" / "portal" / "authority-decisions"
        )
        self._execution_key = execution_key
        self._effect_key = effect_key

    def _request_path(self, request_id: str) -> Path:
        if not _REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError("authority request id contains unsupported characters")
        return self.inbox / f"{request_id}.json"

    def _load_request(self, request_id: str) -> dict[str, object]:
        path = self._request_path(request_id)
        if not path.is_file():
            raise FileNotFoundError(f"pending authority request not found: {request_id}")
        payload = _load_json(path)
        if payload.get("schema") != _REQUEST_SCHEMA:
            raise ValueError("unexpected authority request schema")
        if _required_text(payload, "request_id", "authority request id") != request_id:
            raise ValueError("authority request id does not match filename")
        if payload.get("state") != "PENDING":
            raise ValueError("authority request is not pending")

        for key, label in (
            ("subject_id", "authority subject id"),
            ("repository", "authority repository"),
            ("ref", "authority ref"),
            ("lineage_id", "authority lineage id"),
            ("work_fingerprint", "authority work fingerprint"),
            ("holder", "authority holder"),
            ("operation", "authority operation"),
            ("effect_class", "authority effect class"),
            ("state_db", "authority state database"),
            ("target", "authority target"),
            ("summary", "authority summary"),
            ("source", "authority source"),
        ):
            _required_text(payload, key, label)
        exact_head = _required_text(payload, "exact_head", "authority exact head")
        if not _SHA40_RE.fullmatch(exact_head):
            raise ValueError("authority exact head must be lowercase 40-hex")
        fingerprint = _required_text(
            payload,
            "work_fingerprint",
            "authority work fingerprint",
        )
        if not _SHA256_RE.fullmatch(fingerprint):
            raise ValueError("authority work fingerprint must be lowercase sha256")
        token = _required_int(payload, "fencing_token", "authority fencing token")
        if token <= 0:
            raise ValueError("authority fencing token must be positive")
        _required_number(payload, "created_at", "authority created_at")
        execution_request = payload.get("execution_request")
        if execution_request is not None and not isinstance(
            execution_request,
            Mapping,
        ):
            raise ValueError("authority execution_request must be an object or null")
        return payload

    def _state_db_path(self, payload: Mapping[str, object]) -> Path:
        relative = Path(
            _required_text(payload, "state_db", "authority state database")
        )
        if relative.is_absolute():
            raise ValueError("authority state database must be runtime-relative")
        candidate = (self.runtime_root / relative).resolve()
        try:
            candidate.relative_to(self.runtime_root)
        except ValueError as exc:
            raise ValueError(
                "authority state database escapes the runtime root"
            ) from exc
        if not candidate.is_file():
            raise ValueError("authority state database is unavailable")
        return candidate

    def _validate_against_claim(
        self,
        payload: Mapping[str, object],
        *,
        now: float,
    ) -> None:
        state_db = self._state_db_path(payload)
        lineage_id = _required_text(payload, "lineage_id", "authority lineage id")
        fingerprint = _required_text(
            payload,
            "work_fingerprint",
            "authority work fingerprint",
        )
        with sqlite3.connect(f"file:{state_db.as_posix()}?mode=ro", uri=True) as db:
            row = db.execute(
                """
                SELECT status, work_json
                FROM recursive_work_state
                WHERE lineage_id = ? AND work_fingerprint = ?
                """,
                (lineage_id, fingerprint),
            ).fetchone()
            if row is None:
                raise ValueError("durable claimed work is missing")
            if str(row[0]) != "CLAIMED":
                raise ValueError("authority request requires CLAIMED durable work")
            try:
                work = json.loads(str(row[1]))
            except json.JSONDecodeError as exc:
                raise ValueError("durable claimed work JSON is invalid") from exc
            if not isinstance(work, Mapping):
                raise ValueError("durable claimed work must be an object")
            claim = work.get("payload")
            if not isinstance(claim, Mapping):
                raise ValueError("durable claimed work payload is missing")
            if claim.get("schema") != "PROJECT_RUNNER_BOUND_PLAN_CLAIM_V1":
                raise ValueError("authority request requires a bound-plan claim")
            if claim.get("execution_authority") is not False:
                raise ValueError("claimed work unexpectedly carries execution authority")
            if claim.get("protected_effects_authorized") is not False:
                raise ValueError(
                    "claimed work unexpectedly carries protected-effect authority"
                )
            selected = claim.get("selected")
            if not isinstance(selected, Mapping):
                raise ValueError("claimed work selected payload is missing")

            expected = {
                "subject_id": claim.get("subject_id"),
                "repository": claim.get("repository"),
                "ref": claim.get("ref"),
                "exact_head": claim.get("exact_head"),
                "operation": selected.get("action"),
            }
            labels = {
                "subject_id": "subject id",
                "repository": "repository",
                "ref": "ref",
                "exact_head": "exact head",
                "operation": "operation",
            }
            for key, expected_value in expected.items():
                if payload.get(key) != expected_value:
                    raise ValueError(
                        f"authority request {labels[key]} does not match durable claim"
                    )

            effect_ceiling = selected.get("effect_ceiling")
            if not isinstance(effect_ceiling, str):
                raise ValueError("durable claim effect ceiling is missing")
            effect_class = _required_text(
                payload,
                "effect_class",
                "authority effect class",
            )
            allowed = _ALLOWED_EFFECTS_BY_CEILING.get(effect_ceiling)
            if allowed is None or effect_class not in allowed:
                raise ValueError(
                    "authority request effect class exceeds durable claim effect ceiling"
                )

            lease = db.execute(
                """
                SELECT holder, fencing_token, expires_at, completed
                FROM leases
                WHERE work_fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
            if lease is None:
                raise ValueError("authority request durable lease is missing")
            holder = _required_text(payload, "holder", "authority holder")
            token = _required_int(
                payload,
                "fencing_token",
                "authority fencing token",
            )
            if str(lease[0]) != holder:
                raise ValueError("authority request holder does not match durable lease")
            if int(lease[1]) != token:
                raise ValueError(
                    "authority request fencing token does not match durable lease"
                )
            if bool(lease[3]):
                raise ValueError("authority request durable lease is completed")
            if now >= float(lease[2]):
                raise ValueError("authority request durable lease is expired")

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(path)

    def _resolve_request(
        self,
        request_id: str,
        *,
        decision: str,
    ) -> Path:
        source = self._request_path(request_id)
        target = (
            self.inbox
            / "resolved"
            / decision.lower()
            / f"{request_id}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        return target

    def _execution_key_value(self) -> bytes:
        if self._execution_key is not None:
            if not self._execution_key:
                raise ValueError("execution authority key is required")
            return self._execution_key
        return execution_authority_key_from_environment()

    def _effect_key_value(self) -> bytes | None:
        if self._effect_key is not None:
            return self._effect_key or None
        return effect_authority_key_from_environment()

    def create_request(
        self,
        *,
        state_db: Path,
        subject_id: str,
        repository: str,
        ref: str,
        exact_head: str,
        lineage_id: str,
        work_fingerprint: str,
        fencing_token: int,
        holder: str,
        operation: str,
        effect_class: str,
        execution_request: Mapping[str, object] | None,
        target: str,
        summary: str,
        source: str,
        now: float,
    ) -> dict[str, object]:
        state_db = Path(state_db).resolve()
        try:
            state_db_relative = state_db.relative_to(self.runtime_root).as_posix()
        except ValueError as exc:
            raise ValueError(
                "authority request state database must be inside the runtime root"
            ) from exc

        seed = {
            "state_db": state_db_relative,
            "lineage_id": lineage_id,
            "work_fingerprint": work_fingerprint,
            "fencing_token": fencing_token,
            "operation": operation,
            "effect_class": effect_class,
            "execution_request": (
                dict(execution_request)
                if execution_request is not None
                else None
            ),
        }
        request_id = "authority-" + _sha256(seed)[:24]
        payload: dict[str, object] = {
            "schema": _REQUEST_SCHEMA,
            "request_id": request_id,
            "subject_id": subject_id,
            "repository": repository,
            "ref": ref,
            "exact_head": exact_head,
            "lineage_id": lineage_id,
            "work_fingerprint": work_fingerprint,
            "fencing_token": fencing_token,
            "holder": holder,
            "operation": operation,
            "effect_class": effect_class,
            "execution_request": (
                dict(execution_request)
                if execution_request is not None
                else None
            ),
            "state_db": state_db_relative,
            "target": target,
            "summary": summary,
            "source": source,
            "created_at": float(now),
            "state": "PENDING",
        }
        self._validate_against_claim(payload, now=float(now))

        path = self._request_path(request_id)
        if path.exists():
            existing = _load_json(path)
            if existing != payload:
                raise ValueError(
                    "authority request id collision with different payload"
                )
            return {
                "request_id": request_id,
                "request_path": str(path),
                "created": False,
            }

        self._write_json_atomic(path, payload)
        return {
            "request_id": request_id,
            "request_path": str(path),
            "created": True,
        }

    def approve(
        self,
        request_id: str,
        *,
        issuer: str = "portal-desktop-user-approval",
        now: float,
        valid_for_seconds: float = 300.0,
    ) -> dict[str, object]:
        if not issuer.strip():
            raise ValueError("authority issuer is required")
        if (
            isinstance(valid_for_seconds, bool)
            or not isinstance(valid_for_seconds, (int, float))
            or valid_for_seconds <= 0
            or valid_for_seconds > 900
        ):
            raise ValueError(
                "authority validity must be greater than zero and at most 900 seconds"
            )
        payload = self._load_request(request_id)
        self._validate_against_claim(payload, now=float(now))

        execution_key = self._execution_key_value()
        effect_class = _required_text(
            payload,
            "effect_class",
            "authority effect class",
        )
        execution_request = payload.get("execution_request")
        grant_id = f"desktop-exec-{request_id}"
        execution_document = sign_evidence(
            {
                "schema": "PROJECT_RUNNER_EXECUTION_AUTHORITY_V1",
                "grant_id": grant_id,
                "issuer": issuer.strip(),
                "subject_id": payload["subject_id"],
                "repository": payload["repository"],
                "ref": payload["ref"],
                "exact_head": payload["exact_head"],
                "lineage_id": payload["lineage_id"],
                "work_fingerprint": payload["work_fingerprint"],
                "fencing_token": payload["fencing_token"],
                "operation": payload["operation"],
                "effect_class": effect_class,
                "execution_request": (
                    dict(execution_request)
                    if isinstance(execution_request, Mapping)
                    else None
                ),
                "execution_authorized": True,
                "issued_at": float(now),
                "valid_until": float(now) + float(valid_for_seconds),
            },
            execution_key,
        )
        parsed_execution = parse_execution_grant(
            execution_document,
            key=execution_key,
        )

        effect_document: dict[str, object] | None = None
        effect_key = None
        if effect_class != NO_PROTECTED_EFFECT:
            effect_key = self._effect_key_value()
            if not effect_key:
                raise ValueError("protected-effect authority key is required")
            effect_document = sign_evidence(
                {
                    "schema": "PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_V1",
                    "grant_id": f"desktop-effect-{request_id}",
                    "issuer": issuer.strip(),
                    "subject_id": payload["subject_id"],
                    "repository": payload["repository"],
                    "ref": payload["ref"],
                    "exact_head": payload["exact_head"],
                    "lineage_id": payload["lineage_id"],
                    "work_fingerprint": payload["work_fingerprint"],
                    "fencing_token": payload["fencing_token"],
                    "effect_class": effect_class,
                    "execution_request_sha256": (
                        parsed_execution.execution_request_sha256
                    ),
                    "protected_effects_authorized": True,
                    "issued_at": float(now),
                    "valid_until": float(now) + float(valid_for_seconds),
                },
                effect_key,
            )
            parse_effect_grant(effect_document, key=effect_key)

        grant_dir = self.grants_root / request_id
        execution_path = grant_dir / "execution-grant.json"
        effect_path = grant_dir / "protected-effect-grant.json"
        self._write_json_atomic(execution_path, execution_document)
        if effect_document is not None:
            self._write_json_atomic(effect_path, effect_document)

        decision = {
            "schema": _DECISION_SCHEMA,
            "request_id": request_id,
            "decision": "APPROVED",
            "issuer": issuer.strip(),
            "decided_at": float(now),
            "request_sha256": _sha256(payload),
            "execution_grant_sha256": parsed_execution.sha256,
            "effect_grant_sha256": (
                parse_effect_grant(effect_document, key=effect_key).sha256
                if effect_document is not None and effect_key is not None
                else None
            ),
            "execution_performed": False,
        }
        self._write_json_atomic(
            self.decisions_root / f"{request_id}.json",
            decision,
        )
        resolved = self._resolve_request(request_id, decision="approved")
        return {
            "decision": "APPROVED",
            "execution_grant": str(execution_path),
            "effect_grant": (
                str(effect_path) if effect_document is not None else None
            ),
            "resolved_request": str(resolved),
            "execution_performed": False,
        }

    def deny(
        self,
        request_id: str,
        *,
        issuer: str = "portal-desktop-user-approval",
        now: float,
    ) -> dict[str, object]:
        if not issuer.strip():
            raise ValueError("authority issuer is required")
        payload = self._load_request(request_id)
        decision = {
            "schema": _DECISION_SCHEMA,
            "request_id": request_id,
            "decision": "DENIED",
            "issuer": issuer.strip(),
            "decided_at": float(now),
            "request_sha256": _sha256(payload),
            "execution_grant_sha256": None,
            "effect_grant_sha256": None,
            "execution_performed": False,
        }
        self._write_json_atomic(
            self.decisions_root / f"{request_id}.json",
            decision,
        )
        resolved = self._resolve_request(request_id, decision="denied")
        return {
            "decision": "DENIED",
            "resolved_request": str(resolved),
            "execution_performed": False,
        }
