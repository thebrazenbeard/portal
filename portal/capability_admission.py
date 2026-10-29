"""Model-neutral, source-only capability admission boundary (v0).

This module does not discover, install, invoke, or dispatch a tool. It separates
a reviewed source definition from an ephemeral host advertisement and a
request-scoped grant check. Only READ_ONLY requests can be admitted by v0;
even a successful decision is **not** an execution or provider receipt.

The host MUST supply a trusted, current verifier for the grant/consent decision
at attempted use. It must never accept a verifier from model-generated input.
No global personality, model identity, participant, consent or context defaults
are defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Callable


_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_ADAPTER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EFFECT_CLASSES = frozenset({"READ_ONLY", "PROTECTED_EFFECT"})
_CONTEXT_POLICIES = frozenset({"GENERAL_READ", "ADULT_ELIGIBILITY_REQUIRED"})


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _finite_time(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite numeric time")
    return float(value)


def _effect(value: str) -> str:
    value = _required(value, "effect_class")
    if value not in _EFFECT_CLASSES:
        raise ValueError("unsupported effect_class")
    return value


@dataclass(frozen=True)
class CapabilityDefinition:
    """Versioned declaration of implemented source surface, not an advertisement."""

    capability_id: str
    version: str
    operation: str
    effect_class: str
    input_schema_id: str
    output_schema_id: str
    source_repository: str
    source_commit: str
    privacy_class: str
    context_policy: str

    def __post_init__(self) -> None:
        for field in (
            "capability_id", "version", "operation", "input_schema_id",
            "output_schema_id", "source_repository", "privacy_class",
        ):
            object.__setattr__(self, field, _required(getattr(self, field), field))
        object.__setattr__(self, "effect_class", _effect(self.effect_class))
        if self.context_policy not in _CONTEXT_POLICIES:
            raise ValueError("unsupported context_policy")
        if not _SOURCE_SHA.fullmatch(self.source_commit):
            raise ValueError("source_commit must be exact 40-character Git SHA")


@dataclass(frozen=True)
class RuntimeAdvertisement:
    """Host-observed availability; not permission to invoke an operation."""

    capability_id: str
    version: str
    source_commit: str
    adapter_id: str
    adapter_digest: str
    host_id: str
    observed_at: float
    expires_at: float
    available: bool
    attached: bool
    evidence_ref: str

    def __post_init__(self) -> None:
        for field in ("capability_id", "version", "adapter_id", "host_id", "evidence_ref"):
            object.__setattr__(self, field, _required(getattr(self, field), field))
        if not _SOURCE_SHA.fullmatch(self.source_commit):
            raise ValueError("source_commit must be exact 40-character Git SHA")
        if not _ADAPTER_DIGEST.fullmatch(self.adapter_digest):
            raise ValueError("adapter_digest must be a lowercase sha256 digest")
        for field in ("observed_at", "expires_at"):
            object.__setattr__(self, field, _finite_time(getattr(self, field), field))
        if self.expires_at <= self.observed_at:
            raise ValueError("advertisement must expire after observation")
        if type(self.available) is not bool or type(self.attached) is not bool:
            raise ValueError("advertisement availability flags must be booleans")


@dataclass(frozen=True)
class CapabilityInvocation:
    """Caller input scoped to a model and an opaque participant/context reference."""

    request_id: str
    capability_id: str
    capability_version: str
    operation: str
    required_effect: str
    caller_model_id: str
    participant_ref: str
    context_ref: str
    adapter_id: str
    host_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for field in (
            "request_id", "capability_id", "capability_version", "operation",
            "caller_model_id", "participant_ref", "context_ref", "adapter_id",
            "host_id", "idempotency_key",
        ):
            object.__setattr__(self, field, _required(getattr(self, field), field))
        object.__setattr__(self, "required_effect", _effect(self.required_effect))


@dataclass(frozen=True)
class CapabilityGrant:
    """Opaque scoped grant record requiring independent current host verification."""

    grant_ref: str
    capability_id: str
    capability_version: str
    operation: str
    effect_class: str
    caller_model_id: str
    participant_ref: str
    context_ref: str
    consent_ref: str
    host_id: str
    adapter_id: str
    adapter_digest: str
    expires_at: float
    revoked: bool

    def __post_init__(self) -> None:
        for field in (
            "grant_ref", "capability_id", "capability_version", "operation",
            "caller_model_id", "participant_ref", "context_ref", "consent_ref",
            "host_id", "adapter_id",
        ):
            object.__setattr__(self, field, _required(getattr(self, field), field))
        if not _ADAPTER_DIGEST.fullmatch(self.adapter_digest):
            raise ValueError("adapter_digest must be a lowercase sha256 digest")
        object.__setattr__(self, "effect_class", _effect(self.effect_class))
        object.__setattr__(self, "expires_at", _finite_time(self.expires_at, "expires_at"))
        if type(self.revoked) is not bool:
            raise ValueError("revoked must be boolean")


@dataclass(frozen=True)
class CapabilityGateReceipt:
    """Minimal admission receipt; deliberately NOT an execution or public Bus receipt."""

    request_digest: str
    capability_digest: str
    reason: str
    authorization: str
    effect_status: str = "NOT_EXECUTED"
    evidence_class: str = "SOURCE_GATE_DECISION"


@dataclass(frozen=True)
class CapabilityAdmission:
    allowed: bool
    reason: str
    receipt: CapabilityGateReceipt


GrantVerifier = Callable[[CapabilityGrant, CapabilityInvocation, float], bool]
ContextVerifier = Callable[[CapabilityInvocation, CapabilityDefinition, float], bool]


def _digest_fields(namespace: str, *fields: str) -> str:
    """Domain-separated digest of canonical UTF-8 fields, never raw receipt data.

    JSON array framing prevents ambiguous concatenations. This digest is an
    internal correlation key, NOT anonymization or a public Bus payload.
    """

    canonical = json.dumps([namespace, *fields], ensure_ascii=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _request_digest(request: CapabilityInvocation, definition: CapabilityDefinition) -> str:
    # The request ID by itself can be reused across models, participants or
    # contexts. Bind the entire requested authority scope and reviewed source.
    return _digest_fields(
        "portal-capability-request/v0",
        request.request_id,
        request.capability_id,
        request.capability_version,
        request.operation,
        request.required_effect,
        request.caller_model_id,
        request.participant_ref,
        request.context_ref,
        request.adapter_id,
        request.host_id,
        request.idempotency_key,
        definition.source_commit,
    )


def admit_capability(
    request: CapabilityInvocation,
    *,
    definition: CapabilityDefinition,
    advertisement: RuntimeAdvertisement | None,
    grant: CapabilityGrant | None,
    verify_grant: GrantVerifier | None,
    verify_context: ContextVerifier | None,
    now: float,
) -> CapabilityAdmission:
    """Check exact source/runtime/scope/consent before **read-only** admission.

    The caller must bind `verify_grant` to its authoritative host-side consent
    provider, which checks revocation at use. Nothing here invokes an adapter,
    retries effects, persists receipts, or makes a checked decision durable.
    """

    now = _finite_time(now, "now")

    def decision(allowed: bool, reason: str) -> CapabilityAdmission:
        receipt = CapabilityGateReceipt(
            request_digest=_request_digest(request, definition),
            capability_digest=_digest_fields(
                "portal-capability-definition/v0", definition.capability_id,
                definition.version, definition.source_commit,
            ),
            reason=reason,
            authorization="ALLOWED" if allowed else "DENIED",
        )
        return CapabilityAdmission(allowed=allowed, reason=reason, receipt=receipt)

    if (
        request.capability_id != definition.capability_id
        or request.capability_version != definition.version
        or request.operation != definition.operation
        or request.required_effect != definition.effect_class
    ):
        return decision(False, "DEFINITION_MISMATCH")

    # V0 is intentionally incapable of dispatching a protected effect.
    if request.required_effect != "READ_ONLY":
        return decision(False, "PROTECTED_EFFECT_NOT_IMPLEMENTED")

    if not isinstance(advertisement, RuntimeAdvertisement):
        return decision(False, "NO_CURRENT_RUNTIME")
    if (
        advertisement.capability_id != definition.capability_id
        or advertisement.version != definition.version
        or advertisement.source_commit != definition.source_commit
        or advertisement.adapter_id != request.adapter_id
        or advertisement.host_id != request.host_id
    ):
        return decision(False, "RUNTIME_IDENTITY_MISMATCH")
    if (
        not advertisement.available
        or not advertisement.attached
        or not (advertisement.observed_at <= now < advertisement.expires_at)
    ):
        return decision(False, "NO_CURRENT_RUNTIME")

    if not isinstance(grant, CapabilityGrant):
        return decision(False, "NO_GRANT")
    if (
        grant.capability_id != request.capability_id
        or grant.capability_version != request.capability_version
        or grant.operation != request.operation
        or grant.effect_class != request.required_effect
        or grant.caller_model_id != request.caller_model_id
        or grant.participant_ref != request.participant_ref
        or grant.context_ref != request.context_ref
        or grant.host_id != request.host_id
        or grant.adapter_id != request.adapter_id
        or grant.adapter_digest != advertisement.adapter_digest
    ):
        return decision(False, "GRANT_SCOPE_MISMATCH")
    if grant.revoked or now >= grant.expires_at:
        return decision(False, "GRANT_REVOKED_OR_EXPIRED")
    if verify_grant is None:
        return decision(False, "NO_TRUSTED_GRANT_VERIFIER")
    try:
        if verify_grant(grant, request, now) is not True:
            return decision(False, "GRANT_NOT_CURRENTLY_VERIFIED")
    except Exception:
        return decision(False, "GRANT_NOT_CURRENTLY_VERIFIED")

    # Check private context eligibility only after authenticated grant scope;
    # do not expose an eligibility oracle to unadmitted callers.
    if definition.context_policy == "ADULT_ELIGIBILITY_REQUIRED":
        if verify_context is None:
            return decision(False, "NO_TRUSTED_CONTEXT_VERIFIER")
        try:
            if verify_context(request, definition, now) is not True:
                return decision(False, "CONTEXT_NOT_CURRENTLY_ELIGIBLE")
        except Exception:
            return decision(False, "CONTEXT_NOT_CURRENTLY_ELIGIBLE")
    return decision(True, "ADMITTED_READ_ONLY")
