"""Bounded multi-repository candidate planner; does not dispatch workers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import re

from .registered_checkout_intake import RegisteredCheckout
from .discovery import RepositoryInventoryItem

_SHA40 = re.compile(r"^[a-f0-9]{40}$")


@dataclass(frozen=True)
class LocalLane:
    repository: str
    subject_id: str
    head: str
    disposition: str


def plan_local_portfolio_lanes(
    *, registered: tuple[RegisteredCheckout, ...],
    owned_inventory: tuple[RepositoryInventoryItem, ...],
    remote_heads: Mapping[str, str], inventory_authenticated: bool,
    observed_subject_ids: set[str], delegated_subject_ids: set[str],
    capacity: int,
) -> tuple[LocalLane, ...]:
    """Make an advisory plan, never equate a plan with worker admission.

    External authenticated inventory and remote HEADs must be supplied and
    independently verified by the host before any resulting wave is admitted.
    """
    if type(capacity) is not int or capacity < 0:
        raise ValueError("physical worker capacity must be nonnegative")
    owned: dict[str, RepositoryInventoryItem] = {}
    for repo in owned_inventory:
        key = repo.full_name.casefold()
        if key in owned:
            raise ValueError("duplicate owner inventory")
        owned[key] = repo
    remotes: dict[str, str] = {}
    for key, sha in remote_heads.items():
        normal = key.casefold()
        if normal in remotes:
            raise ValueError("duplicate remote head identity")
        remotes[normal] = sha
    observed = {name.casefold() for name in observed_subject_ids}
    delegated = {name.casefold() for name in delegated_subject_ids}
    results: list[LocalLane] = []
    seen: set[str] = set()
    slots = 0
    for item in sorted(registered, key=lambda x: x.repository.casefold()):
        key = item.repository.casefold()
        subject = item.subject_id.casefold()
        if key in seen:
            raise ValueError("duplicate registered repository")
        seen.add(key)
        match = owned.get(key)
        remote_head = remotes.get(key)
        if item.already_observed or subject in observed:
            decision = "HOLD_PREVIOUSLY_OBSERVED"
        elif subject in delegated:
            decision = "HOLD_DELEGATED"
        elif match is None:
            decision = "HOLD_NOT_OWNED"
        elif match.archived:
            decision = "HOLD_ARCHIVED"
        elif match.private and not inventory_authenticated:
            decision = "HOLD_UNAUTHENTICATED_PRIVATE"
        elif item.source_ref != match.default_branch:
            decision = "HOLD_WRONG_BRANCH"
        elif not isinstance(remote_head, str) or not _SHA40.fullmatch(remote_head):
            decision = "HOLD_REMOTE_UNVERIFIED"
        elif item.head != remote_head:
            decision = "HOLD_STALE_HEAD"
        elif slots >= capacity:
            decision = "DEFERRED_CAPACITY"
        else:
            decision = "CANDIDATE_FOR_SCHEDULER"
            slots += 1
        results.append(LocalLane(item.repository, item.subject_id,
                                 item.head, decision))
    return tuple(results)
