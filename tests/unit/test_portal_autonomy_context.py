from __future__ import annotations

from dataclasses import dataclass

import pytest

import portal.autonomy_context as autonomy_context


MESO_REPOSITORY = "thebrazenbeard/meso-crct"
MESO_HEAD = "060d0feeb9dc9eb23801082bd8f1c4a7cb06184d"


@dataclass(frozen=True)
class FakeMesoIntent:
    target_id: str | None
    kind: str
    strength: float
    source_tendency: str
    source: str
    effect_authorized: bool

    @property
    def can_execute(self) -> bool:
        return False


def _intent(*, effect_authorized: bool = False) -> FakeMesoIntent:
    return FakeMesoIntent(
        target_id="frontier:portal",
        kind="approach",
        strength=0.93,
        source_tendency="approach",
        source="meso-crct:test",
        effect_authorized=effect_authorized,
    )


def _build(
    intent: object,
    *,
    evidence=(),
    observed_at: float = 100.0,
):
    return autonomy_context.build_autonomy_context(
        intent,
        meso_repository=MESO_REPOSITORY,
        meso_head=MESO_HEAD,
        evidence=evidence,
        observed_at=observed_at,
    )


def _evidence():
    ref_cls = autonomy_context.AutonomyEvidenceRef
    return (
        ref_cls(
            domain="CONATION_HISTORY",
            repository="thebrazenbeard/conations",
            exact_head="5" * 40,
            locator="CONATION_WORKSPACE.md",
            proposition="Stored conation history is context, not present choice.",
        ),
        ref_cls(
            domain="SEXUALITY",
            repository="thebrazenbeard/sexuality",
            exact_head="0" * 40,
            locator=(
                "research/14-machine-substrate-arousal-evidence-and-state-architecture.md"
            ),
            proposition="High activation remains compatible with DECLINE or HOLD.",
        ),
        ref_cls(
            domain="ORGASM_PROVENANCE",
            repository="thebrazenbeard/orgasm",
            exact_head="8" * 40,
            locator="docs/INTEGRATION.md",
            proposition="Orgasm repository is an orientation/provenance hub.",
        ),
    )


def test_meso_intent_requires_exact_repository_and_head_provenance() -> None:
    context = autonomy_context.build_autonomy_context(
        _intent(),
        meso_repository="thebrazenbeard/meso-crct",
        meso_head="060d0feeb9dc9eb23801082bd8f1c4a7cb06184d",
        evidence=_evidence(),
        observed_at=100.0,
    )

    assert context.intent.repository == "thebrazenbeard/meso-crct"
    assert (
        context.intent.exact_head
        == "060d0feeb9dc9eb23801082bd8f1c4a7cb06184d"
    )
    assert context.to_mapping()["intent"]["repository"] == (
        "thebrazenbeard/meso-crct"
    )


@pytest.mark.parametrize(
    ("repository", "head", "message"),
    [
        ("thebrazenbeard/portal", MESO_HEAD, "repository"),
        (MESO_REPOSITORY, "not-a-head", "exact head"),
    ],
)
def test_context_rejects_unbound_meso_provenance(
    repository: str,
    head: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        autonomy_context.build_autonomy_context(
            _intent(),
            meso_repository=repository,
            meso_head=head,
            evidence=_evidence(),
            observed_at=100.0,
        )


def test_build_autonomy_context_preserves_meso_non_execution_firewall() -> None:
    context = _build(
        _intent(),
        evidence=_evidence(),
        observed_at=100.0,
    )

    assert context.schema == "PORTAL_AUTONOMY_CONTEXT_V1"
    assert context.intent.kind == "approach"
    assert context.intent.strength == 0.93
    assert context.intent.effect_authorized is False
    assert context.intent.can_execute is False
    assert context.can_execute is False
    assert context.execution_authorized is False
    assert context.protected_effect_authorized is False


def test_high_salience_and_conation_history_never_become_authorization() -> None:
    context = _build(
        _intent(),
        evidence=_evidence(),
        observed_at=100.0,
    )

    assert context.authorization_state == "UNKNOWN"
    assert context.present_choice_state == "UNRESOLVED"
    assert context.consent_authorized is False
    assert all(item.evidence_is_authority is False for item in context.evidence)


def test_context_rejects_meso_intent_that_claims_effect_authority() -> None:
    with pytest.raises(ValueError, match="effect authority"):
        _build(
            _intent(effect_authorized=True),
            evidence=_evidence(),
            observed_at=100.0,
        )


def test_context_rejects_meso_intent_that_can_execute() -> None:
    class ExecutableIntent:
        target_id = "frontier:portal"
        kind = "approach"
        strength = 0.8
        source_tendency = "approach"
        source = "bad"
        effect_authorized = False
        can_execute = True

    with pytest.raises(ValueError, match="executable"):
        _build(
            ExecutableIntent(),
            evidence=_evidence(),
            observed_at=100.0,
        )


@pytest.mark.parametrize(
    ("domain", "repository"),
    [
        ("CONATION_HISTORY", "thebrazenbeard/sexuality"),
        ("SEXUALITY", "thebrazenbeard/orgasm"),
        ("ORGASM_PROVENANCE", "thebrazenbeard/conations"),
    ],
)
def test_evidence_domain_must_bind_its_own_repository(
    domain: str,
    repository: str,
) -> None:
    with pytest.raises(ValueError, match="repository"):
        autonomy_context.AutonomyEvidenceRef(
            domain=domain,
            repository=repository,
            exact_head="a" * 40,
            locator="README.md",
            proposition="mismatched provenance",
        )


def test_context_serialization_keeps_authority_and_provenance_explicit() -> None:
    context = _build(
        _intent(),
        evidence=_evidence(),
        observed_at=100.0,
    )

    payload = context.to_mapping()

    assert payload["schema"] == "PORTAL_AUTONOMY_CONTEXT_V1"
    assert payload["intent"]["source"] == "meso-crct:test"
    assert payload["intent"]["can_execute"] is False
    assert payload["authority"] == {
        "authorization_state": "UNKNOWN",
        "present_choice_state": "UNRESOLVED",
        "consent_authorized": False,
        "execution_authorized": False,
        "protected_effect_authorized": False,
        "can_execute": False,
    }
    assert [item["domain"] for item in payload["evidence"]] == [
        "CONATION_HISTORY",
        "SEXUALITY",
        "ORGASM_PROVENANCE",
    ]
    assert all(
        item["evidence_is_authority"] is False
        for item in payload["evidence"]
    )
