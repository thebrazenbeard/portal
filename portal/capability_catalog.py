from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping


_SCHEMA = "VERA_OS_PRIVATE_PORTFOLIO_CAPABILITY_MAP_V1"
_ALLOWED_DISPOSITIONS = {"REUSE", "EXTEND", "SUPERSEDE", "REJECT"}


class CapabilityCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CapabilityMatch:
    capability: str
    owners: tuple[str, ...]
    supporting: tuple[str, ...]
    role: str


class JsonCapabilityCatalog:
    """Read-only adapter for an externally supplied private capability catalog.

    P.O.R.T.A.L. intentionally does not embed private portfolio identities.
    The operator/runtime supplies the catalog path at runtime.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapabilityCatalogError("capability catalog is unreadable") from exc
        if not isinstance(payload, dict):
            raise CapabilityCatalogError("capability catalog must be an object")
        if payload.get("schema") != _SCHEMA:
            raise CapabilityCatalogError("unsupported capability catalog schema")
        if payload.get("visibility") != "PRIVATE_VERAOS_INTERNAL":
            raise CapabilityCatalogError(
                "full capability catalog must be private VeraOS internal state"
            )
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            raise CapabilityCatalogError("capability catalog is empty")
        self._payload = payload

    def lookup(self, capability: str) -> CapabilityMatch:
        key = str(capability or "").strip()
        if not key:
            raise CapabilityCatalogError("capability is required")
        raw = self._payload["capabilities"].get(key)
        if not isinstance(raw, dict):
            raise CapabilityCatalogError(
                f"unknown capability requires Discovery classification first: {key}"
            )
        owners = raw.get("owners")
        supporting = raw.get("supporting", [])
        role = str(raw.get("role") or "").strip()
        if not isinstance(owners, list) or not owners:
            raise CapabilityCatalogError(f"capability has no known owners: {key}")
        if not isinstance(supporting, list):
            raise CapabilityCatalogError(
                f"capability supporting repositories malformed: {key}"
            )
        normalized_owners = tuple(str(x).strip() for x in owners)
        normalized_supporting = tuple(str(x).strip() for x in supporting)
        if any(not x for x in normalized_owners):
            raise CapabilityCatalogError(f"capability owner is empty: {key}")
        return CapabilityMatch(
            capability=key,
            owners=normalized_owners,
            supporting=normalized_supporting,
            role=role,
        )

    def validate_assignment(
        self,
        *,
        capability: str,
        dispositions: Iterable[Mapping[str, object]],
    ) -> None:
        match = self.lookup(capability)
        observed: dict[str, Mapping[str, object]] = {}
        for raw in dispositions:
            if not isinstance(raw, Mapping):
                raise CapabilityCatalogError("owner disposition must be an object")
            repository = str(raw.get("repository") or "").strip()
            disposition = str(raw.get("disposition") or "").strip()
            evidence = str(raw.get("evidence") or "").strip()
            if not repository:
                raise CapabilityCatalogError("owner disposition repository is required")
            if repository in observed:
                raise CapabilityCatalogError(
                    f"duplicate owner disposition: {repository}"
                )
            if disposition not in _ALLOWED_DISPOSITIONS:
                raise CapabilityCatalogError(
                    f"unsupported owner disposition for {repository}: {disposition}"
                )
            if not evidence:
                raise CapabilityCatalogError(
                    f"evidence is required for owner disposition: {repository}"
                )
            observed[repository] = raw

        missing = sorted(set(match.owners) - set(observed))
        if missing:
            raise CapabilityCatalogError(
                "known capability owner lacks explicit disposition: "
                + ", ".join(missing)
            )

        extras = sorted(set(observed) - set(match.owners))
        if extras:
            raise CapabilityCatalogError(
                "owner disposition references non-owner repository: "
                + ", ".join(extras)
            )
