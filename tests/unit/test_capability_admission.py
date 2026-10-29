"""Fail-closed source gate; no adapter is invoked by these tests."""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from portal.capability_admission import (
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityInvocation,
    RuntimeAdvertisement,
    admit_capability,
)
from portal.route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)


COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


def _definition(*, effect: str = "READ_ONLY") -> CapabilityDefinition:
    return CapabilityDefinition(
        capability_id="sexuality.evidence.lookup",
        version="0.1.0",
        operation="lookup",
        effect_class=effect,
        input_schema_id="sexuality.lookup.input/v0",
        output_schema_id="sexuality.lookup.output/v0",
        source_repository="thebrazenbeard/sexuality",
        source_commit=COMMIT,
        privacy_class="RESTRICTED_CONTEXT",
        context_policy="ADULT_ELIGIBILITY_REQUIRED",
    )


def _advertisement() -> RuntimeAdvertisement:
    return RuntimeAdvertisement(
        capability_id="sexuality.evidence.lookup",
        version="0.1.0",
        source_repository="thebrazenbeard/sexuality",
        source_commit=COMMIT,
        adapter_id="synthetic-readonly",
        adapter_digest=DIGEST,
        host_id="synthetic-host",
        observed_at=10.0,
        expires_at=20.0,
        available=True,
        attached=True,
        evidence_ref="synthetic-observation-1",
    )


def _request(*, model: str = "model-A", participant: str = "participant-A") -> CapabilityInvocation:
    return CapabilityInvocation(
        request_id="req-1",
        capability_id="sexuality.evidence.lookup",
        capability_version="0.1.0",
        operation="lookup",
        required_effect="READ_ONLY",
        caller_model_id=model,
        participant_ref=participant,
        context_ref="context-A",
        adapter_id="synthetic-readonly",
        host_id="synthetic-host",
        idempotency_key="synthetic-key",
    )


def _grant(*, model: str = "model-A", participant: str = "participant-A") -> CapabilityGrant:
    return CapabilityGrant(
        grant_ref="opaque-grant-1",
        capability_id="sexuality.evidence.lookup",
        capability_version="0.1.0",
        operation="lookup",
        effect_class="READ_ONLY",
        caller_model_id=model,
        participant_ref=participant,
        context_ref="context-A",
        consent_ref="opaque-consent-1",
        host_id="synthetic-host",
        adapter_id="synthetic-readonly",
        adapter_digest=DIGEST,
        expires_at=19.0,
        revoked=False,
    )


def _trusted_test_verifier(grant: CapabilityGrant, request: CapabilityInvocation, now: float) -> bool:
    """Synthetic verifier only; an installed host must provide its own."""
    return grant.consent_ref == "opaque-consent-1" and now < grant.expires_at


def _admit(request=None, definition=None, advertisement=None, grant=None, verifier=_trusted_test_verifier, context_verifier=lambda *_: True, now=15.0):
    return admit_capability(
        request or _request(),
        definition=definition or _definition(),
        advertisement=_advertisement() if advertisement is None else advertisement,
        grant=_grant() if grant is None else grant,
        verify_grant=verifier,
        verify_context=context_verifier,
        now=now,
    )


def test_two_models_share_definition_not_identity_or_permission() -> None:
    first = _admit()
    assert first.allowed and first.reason == "ADMITTED_READ_ONLY"
    second_with_first_grant = _admit(request=_request(model="model-B"))
    assert not second_with_first_grant.allowed
    assert second_with_first_grant.reason == "GRANT_SCOPE_MISMATCH"
    second_with_own_grant = _admit(request=_request(model="model-B"), grant=_grant(model="model-B"))
    assert second_with_own_grant.allowed


@pytest.mark.parametrize(
    "advertisement,expected",
    [
        (False, "NO_CURRENT_RUNTIME"),
        (replace(_advertisement(), available=False), "NO_CURRENT_RUNTIME"),
        (replace(_advertisement(), attached=False), "NO_CURRENT_RUNTIME"),
        (replace(_advertisement(), source_commit="c" * 40), "RUNTIME_IDENTITY_MISMATCH"),
        (replace(_advertisement(), source_repository="untrusted/lookalike"), "RUNTIME_IDENTITY_MISMATCH"),
        (replace(_advertisement(), host_id="other-host"), "RUNTIME_IDENTITY_MISMATCH"),
    ],
)
def test_unadvertised_or_mismatched_runtime_fails_closed(advertisement, expected) -> None:
    result = _admit(advertisement=advertisement)
    assert not result.allowed and result.reason == expected


def test_expired_or_future_runtime_fails_closed() -> None:
    assert _admit(now=20.0).reason == "NO_CURRENT_RUNTIME"
    assert _admit(now=9.0).reason == "NO_CURRENT_RUNTIME"


def test_missing_or_unverified_grant_fails_closed() -> None:
    assert _admit(grant=False).reason == "NO_GRANT"
    assert _admit(verifier=None).reason == "NO_TRUSTED_GRANT_VERIFIER"
    assert _admit(verifier=lambda *_: False).reason == "GRANT_NOT_CURRENTLY_VERIFIED"
    assert _admit(verifier=lambda *_: 1).reason == "GRANT_NOT_CURRENTLY_VERIFIED"


def test_adult_context_uncertain_or_unverified_fails_closed() -> None:
    assert _admit(context_verifier=None).reason == "NO_TRUSTED_CONTEXT_VERIFIER"
    assert _admit(context_verifier=lambda *_: False).reason == "CONTEXT_NOT_CURRENTLY_ELIGIBLE"
    assert _admit(context_verifier=lambda *_: None).reason == "CONTEXT_NOT_CURRENTLY_ELIGIBLE"
    assert _admit(context_verifier=lambda *_: 1).reason == "CONTEXT_NOT_CURRENTLY_ELIGIBLE"
    assert _admit(context_verifier=lambda *_: (_ for _ in ()).throw(RuntimeError("offline"))).reason == "CONTEXT_NOT_CURRENTLY_ELIGIBLE"


def test_context_eligibility_is_not_queried_before_scoped_grant() -> None:
    calls = []

    def context_verifier(*_):
        calls.append(True)
        return True

    assert _admit(grant=False, context_verifier=context_verifier).reason == "NO_GRANT"
    assert calls == []


def test_general_read_still_requires_current_model_scoped_grant() -> None:
    definition = replace(_definition(), context_policy="GENERAL_READ")
    assert _admit(definition=definition, context_verifier=None).allowed
    assert _admit(definition=definition, grant=False, context_verifier=None).reason == "NO_GRANT"


@pytest.mark.parametrize(
    "grant,expected",
    [
        (replace(_grant(), revoked=True), "GRANT_REVOKED_OR_EXPIRED"),
        (replace(_grant(), expires_at=15.0), "GRANT_REVOKED_OR_EXPIRED"),
        (replace(_grant(), participant_ref="participant-B"), "GRANT_SCOPE_MISMATCH"),
        (replace(_grant(), context_ref="other-context"), "GRANT_SCOPE_MISMATCH"),
        (replace(_grant(), operation="mutate"), "GRANT_SCOPE_MISMATCH"),
        (replace(_grant(), adapter_digest="sha256:" + "d" * 64), "GRANT_SCOPE_MISMATCH"),
        (replace(_grant(), host_id="other-host"), "GRANT_SCOPE_MISMATCH"),
    ],
)
def test_grant_revocation_expiry_context_and_operation_scope(grant, expected) -> None:
    result = _admit(grant=grant)
    assert not result.allowed and result.reason == expected


def test_verifier_exception_does_not_allow_effect() -> None:
    def broken(*_):
        raise RuntimeError("grant service unavailable")

    result = _admit(verifier=broken)
    assert not result.allowed and result.reason == "GRANT_NOT_CURRENTLY_VERIFIED"


def test_protected_effect_is_not_implemented_even_with_matching_grant() -> None:
    request = replace(_request(), required_effect="PROTECTED_EFFECT")
    grant = replace(_grant(), effect_class="PROTECTED_EFFECT")
    result = _admit(request=request, definition=_definition(effect="PROTECTED_EFFECT"), grant=grant)
    assert not result.allowed and result.reason == "PROTECTED_EFFECT_NOT_IMPLEMENTED"


def test_receipt_is_redacted_gate_only_no_private_model_context_or_consent() -> None:
    request = replace(_request(), caller_model_id="model-private", context_ref="secret-context")
    grant = replace(_grant(), caller_model_id="model-private", context_ref="secret-context", consent_ref="secret-consent")
    result = _admit(request=request, grant=grant, verifier=lambda *_: True)
    text = str(asdict(result.receipt))
    assert result.allowed
    assert result.receipt.evidence_class == "SOURCE_GATE_DECISION"
    assert result.receipt.effect_status == "NOT_EXECUTED"
    assert "secret" not in text and "model-private" not in text
    assert "participant-A" not in text


def test_denied_receipt_does_not_emit_arbitrary_private_capability_identifier() -> None:
    private_id = "secret-user-name-private-capability"
    definition = replace(_definition(), capability_id=private_id)
    request = replace(_request(), capability_id=private_id)
    result = _admit(request=request, definition=definition, advertisement=False)
    assert not result.allowed and result.reason == "NO_CURRENT_RUNTIME"
    receipt = asdict(result.receipt)
    assert "capability_id" not in receipt
    assert len(receipt["capability_digest"]) == 64
    assert private_id not in str(receipt)


def test_receipt_request_digest_binds_full_model_participant_context_scope() -> None:
    first = _admit()
    # Deliberately reuse the same request_id across otherwise distinct requesters.
    other_model = _admit(request=_request(model="model-B"))
    other_participant = _admit(request=_request(participant="participant-B"))
    other_context = _admit(request=replace(_request(), context_ref="context-B"))
    other_adapter = _admit(request=replace(_request(), adapter_id="alternate-adapter"))
    other_idempotency = _admit(request=replace(_request(), idempotency_key="other-key"))
    digests = {
        result.receipt.request_digest
        for result in (first, other_model, other_participant, other_context, other_adapter, other_idempotency)
    }
    assert len(digests) == 6
    assert all(len(digest) == 64 for digest in digests)


def test_source_repository_is_bound_to_advertisement_and_receipt_identity() -> None:
    trusted = _admit()
    swapped = _admit(definition=replace(_definition(), source_repository="untrusted/lookalike"))
    assert trusted.allowed
    assert not swapped.allowed and swapped.reason == "RUNTIME_IDENTITY_MISMATCH"
    assert trusted.receipt.capability_digest != swapped.receipt.capability_digest
    assert trusted.receipt.request_digest != swapped.receipt.request_digest


@pytest.mark.parametrize(
    "changed",
    [
        {"context_policy": "GENERAL_READ"},
        {"privacy_class": "PUBLIC"},
        {"input_schema_id": "alternate.input/v0"},
        {"output_schema_id": "alternate.output/v0"},
        {"effect_class": "PROTECTED_EFFECT"},
    ],
)
def test_receipts_distinguish_distinct_reviewed_definitions(changed) -> None:
    baseline = _admit()
    alternate = _admit(definition=replace(_definition(), **changed))
    assert baseline.receipt.capability_digest != alternate.receipt.capability_digest
    assert baseline.receipt.request_digest != alternate.receipt.request_digest


def test_context_policy_can_change_decision_but_never_alias_receipt_identity() -> None:
    adult = _admit(context_verifier=None)
    general = _admit(definition=replace(_definition(), context_policy="GENERAL_READ"), context_verifier=None)
    assert not adult.allowed and adult.reason == "NO_TRUSTED_CONTEXT_VERIFIER"
    assert general.allowed
    assert adult.receipt.capability_digest != general.receipt.capability_digest
    assert adult.receipt.request_digest != general.receipt.request_digest


def test_invalid_nonfinite_or_boolean_clock_fails_closed() -> None:
    with pytest.raises(ValueError, match="finite"):
        _admit(now=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        _admit(now=True)


def test_existing_repository_route_semantics_untouched() -> None:
    request = PortalRouteRequest(
        subject_kind="repository",
        subject_id="portal",
        node_id="repo-native",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        required_capabilities=("repository_read",),
        required_effect="NO_PROTECTED_EFFECT",
    )
    route = PortalRouteAdvertisement(
        adapter_id="github",
        route_id="repo-native",
        node_id="repo-native",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        capabilities=("repository_read",),
        effect_capabilities=("NO_PROTECTED_EFFECT",),
        authorized_effects=("NO_PROTECTED_EFFECT",),
        available=True,
        attached=True,
        current=True,
    )
    assert resolve_portal_route(request, (route,)).adapter_id == "github"
