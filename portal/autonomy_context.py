"""Non-executable autonomy context bridge for P.O.R.T.A.L.

MESO-CRCT may supply a current action-intent proposal. Conations, Sexuality,
and Orgasm may supply bounded evidence/provenance. None of those inputs grants
execution, protected effects, consent, or current-choice authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable, Mapping


_SCHEMA = "PORTAL_AUTONOMY_CONTEXT_V1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_MESO_REPOSITORY = "thebrazenbeard/meso-crct"
_DOMAINS = {
    "CONATION_HISTORY": "thebrazenbeard/conations",
    "SEXUALITY": "thebrazenbeard/sexuality",
    "ORGASM_PROVENANCE": "thebrazenbeard/orgasm",
}


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _kind_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return _text(raw, label="MESO intent kind")


def _strength(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("MESO intent strength must be numeric")
    observed = float(value)
    if not math.isfinite(observed) or observed < 0.0 or observed > 1.0:
        raise ValueError("MESO intent strength must be finite and within [0, 1]")
    return observed


@dataclass(frozen=True, slots=True)
class AutonomyEvidenceRef:
    domain: str
    repository: str
    exact_head: str
    locator: str
    proposition: str

    def __post_init__(self) -> None:
        domain = _text(self.domain, label="evidence domain")
        expected_repository = _DOMAINS.get(domain)
        if expected_repository is None:
            raise ValueError(f"unsupported autonomy evidence domain: {domain}")
        repository = _text(self.repository, label="evidence repository")
        if repository != expected_repository:
            raise ValueError(
                f"{domain} evidence repository must be {expected_repository}"
            )
        exact_head = _text(self.exact_head, label="evidence exact head")
        if not _SHA40.fullmatch(exact_head):
            raise ValueError("evidence exact head must be lowercase 40-hex")
        locator = _text(self.locator, label="evidence locator")
        proposition = _text(self.proposition, label="evidence proposition")
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "exact_head", exact_head)
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "proposition", proposition)

    @property
    def evidence_is_authority(self) -> bool:
        return False

    def to_mapping(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "repository": self.repository,
            "exact_head": self.exact_head,
            "locator": self.locator,
            "proposition": self.proposition,
            "evidence_is_authority": False,
        }


@dataclass(frozen=True, slots=True)
class MesoIntentContext:
    repository: str
    exact_head: str
    target_id: str | None
    kind: str
    strength: float
    source_tendency: str
    source: str
    effect_authorized: bool = False
    can_execute: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "exact_head": self.exact_head,
            "target_id": self.target_id,
            "kind": self.kind,
            "strength": self.strength,
            "source_tendency": self.source_tendency,
            "source": self.source,
            "effect_authorized": False,
            "can_execute": False,
        }


@dataclass(frozen=True, slots=True)
class AutonomyContextEnvelope:
    intent: MesoIntentContext
    evidence: tuple[AutonomyEvidenceRef, ...]
    observed_at: float
    schema: str = _SCHEMA
    authorization_state: str = "UNKNOWN"
    present_choice_state: str = "UNRESOLVED"
    consent_authorized: bool = False
    execution_authorized: bool = False
    protected_effect_authorized: bool = False

    @property
    def can_execute(self) -> bool:
        return False

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "observed_at": self.observed_at,
            "intent": self.intent.to_mapping(),
            "evidence": [item.to_mapping() for item in self.evidence],
            "authority": {
                "authorization_state": self.authorization_state,
                "present_choice_state": self.present_choice_state,
                "consent_authorized": False,
                "execution_authorized": False,
                "protected_effect_authorized": False,
                "can_execute": False,
            },
        }


def bind_meso_intent(
    intent: object,
    *,
    repository: str,
    exact_head: str,
) -> MesoIntentContext:
    """Bind a MESO IntentProposal without importing MESO as a runtime dependency."""

    bound_repository = _text(repository, label="MESO repository")
    if bound_repository != _MESO_REPOSITORY:
        raise ValueError(
            f"MESO repository must be {_MESO_REPOSITORY}"
        )
    bound_head = _text(exact_head, label="MESO exact head")
    if not _SHA40.fullmatch(bound_head):
        raise ValueError("MESO exact head must be lowercase 40-hex")

    if getattr(intent, "effect_authorized", None) is not False:
        raise ValueError("MESO intent must carry no effect authority")
    if getattr(intent, "can_execute", None) is not False:
        raise ValueError("MESO intent must be non-executable")

    target_id = getattr(intent, "target_id", None)
    if target_id is not None:
        target_id = _text(target_id, label="MESO intent target id")

    return MesoIntentContext(
        repository=bound_repository,
        exact_head=bound_head,
        target_id=target_id,
        kind=_kind_text(getattr(intent, "kind", None)),
        strength=_strength(getattr(intent, "strength", None)),
        source_tendency=_text(
            getattr(intent, "source_tendency", None),
            label="MESO source tendency",
        ),
        source=_text(
            getattr(intent, "source", None),
            label="MESO intent source",
        ),
    )


def build_autonomy_context(
    intent: object,
    *,
    meso_repository: str,
    meso_head: str,
    evidence: Iterable[AutonomyEvidenceRef] = (),
    observed_at: float,
) -> AutonomyContextEnvelope:
    """Build a context-only autonomy envelope.

    This bridge deliberately has no parameter capable of granting consent,
    current-choice, execution, or protected-effect authority.
    """

    if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
        raise ValueError("observed_at must be numeric")
    timestamp = float(observed_at)
    if not math.isfinite(timestamp):
        raise ValueError("observed_at must be finite")

    bound_evidence = tuple(evidence)
    if any(not isinstance(item, AutonomyEvidenceRef) for item in bound_evidence):
        raise TypeError("autonomy evidence must use AutonomyEvidenceRef")

    return AutonomyContextEnvelope(
        intent=bind_meso_intent(
            intent,
            repository=meso_repository,
            exact_head=meso_head,
        ),
        evidence=bound_evidence,
        observed_at=timestamp,
    )
