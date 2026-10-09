"""Read-only GitHub HEAD and host node binding for local candidate planning."""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import quote

from .registered_checkout_intake import inspect_registered_checkouts
from .multilane_intake import LocalLane, plan_local_portfolio_lanes
from .node_registry import load_execution_nodes

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def plan_authenticated_source_lanes(
    *, index_path: Path, nodes_path: Path, owner: str, catalog: object,
    qualified_slot_limit: int, observed_subject_ids: set[str],
    delegated_subject_ids: set[str],
) -> tuple[LocalLane, ...]:
    """Return an advisory plan; never authorize actual worker execution.

    Caller must supply independently qualified physical slot limit and
    verified resident subject/delegation state. No credentials are written.
    """
    token = getattr(catalog, "token", None)
    if not isinstance(token, str) or not token.strip():
        raise ValueError("authenticated GitHub inventory is required")
    nodes = load_execution_nodes(nodes_path)
    slots = sum(node.max_parallel for node in nodes if node.enabled)
    if (type(qualified_slot_limit) is not int or qualified_slot_limit < 0
            or qualified_slot_limit > slots):
        raise ValueError("qualified capacity cannot exceed enabled node slots")
    registered = inspect_registered_checkouts(
        index_path, owner=owner, observed_subject_ids=observed_subject_ids,
    )
    owned = catalog.list_owned_repositories(owner)
    owner_index = {item.full_name.casefold(): item for item in owned}
    if len(owner_index) != len(owned):
        raise ValueError("duplicate authenticated GitHub inventory")
    head_map: dict[str, str] = {}
    for entry in registered:
        match = owner_index.get(entry.repository.casefold())
        if match is None:
            continue
        repo_owner, repo_name = match.full_name.split("/", 1)
        remote_ref = match.default_branch
        url = (
            f"{catalog.api_base}/repos/"
            f"{quote(repo_owner, safe='')}/{quote(repo_name, safe='')}"
            f"/git/ref/heads/{quote(remote_ref, safe='')}"
        )
        raw = catalog._get_json(url)
        if not isinstance(raw, dict) or raw.get("ref") != f"refs/heads/{remote_ref}":
            raise ValueError("GitHub ref response differs from expected ref")
        object_data = raw.get("object")
        if not isinstance(object_data, dict) or object_data.get("type") != "commit":
            raise ValueError("GitHub ref must directly target a commit")
        sha = object_data.get("sha")
        if not isinstance(sha, str) or not _SHA40.fullmatch(sha):
            raise ValueError("GitHub ref has invalid commit SHA")
        head_map[entry.repository] = sha
    return plan_local_portfolio_lanes(
        registered=registered, owned_inventory=owned,
        remote_heads=head_map, inventory_authenticated=True,
        observed_subject_ids=observed_subject_ids,
        delegated_subject_ids=delegated_subject_ids,
        capacity=qualified_slot_limit,
    )
