"""Source-supported portfolio roles, not qualified runtime bindings.

Each hint is grounded in public PORTAL donor architecture and source READMEs.
A relationship hint does not grant a dependency, worker route, or permission.
"""
from __future__ import annotations

_ROLES = {
    "portal": "Portfolio coordinator / Windows control surface",
    "project-runner": "Exact-subject execution kernel / leases and receipts",
    "vera-mono": "Self-contained Vera runtime implementation lineage",
    "pre-active": "Resident event/turn scheduling donor",
    "volition": "Resident drive/signal source donor",
    "discovery": "Capability discovery / source-currentness gate",
    "physica": "Physics simulation research workload",
    "vera-ark": "Game integration research / unqualified live gameplay",
    "workbridgecommander": "Workstation execution lane attachment donor",
    "blotter": "Shared agent activity / audit substrate",
}

def relationship_hint(repository: str) -> str:
    """Human navigation hint only; never an execution admission criterion."""
    repo = repository.split("/")[-1].casefold()
    return _ROLES.get(repo, "Unclassified: inspect contracts before binding")

def role_counts(repositories: list[str]) -> dict[str, int]:
    observed = {"known_role":0, "needs_source_review":0}
    for name in repositories:
        key = "needs_source_review" if relationship_hint(name).startswith("Unclassified:") else "known_role"
        observed[key] += 1
    return observed
