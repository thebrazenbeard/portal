from __future__ import annotations

from dataclasses import dataclass


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


@dataclass(frozen=True)
class PortalRouteBinding:
    subject_kind: str
    subject_id: str
    adapter_id: str
    route_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_kind", _required(self.subject_kind, "subject_kind"))
        object.__setattr__(self, "subject_id", _required(self.subject_id, "subject_id"))
        object.__setattr__(self, "adapter_id", _required(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "route_id", _required(self.route_id, "route_id"))


@dataclass(frozen=True)
class PortalDispatchRecord:
    subject_kind: str
    subject_id: str
    adapter_id: str
    route_id: str
    state: str
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_kind", _required(self.subject_kind, "subject_kind"))
        object.__setattr__(self, "subject_id", _required(self.subject_id, "subject_id"))
        object.__setattr__(self, "adapter_id", _required(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "route_id", _required(self.route_id, "route_id"))
        object.__setattr__(self, "state", _required(self.state, "dispatch state"))
        if self.evidence_id is not None:
            object.__setattr__(
                self,
                "evidence_id",
                _required(self.evidence_id, "dispatch evidence_id"),
            )
