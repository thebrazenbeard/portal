from __future__ import annotations

import pytest

from portal.adapters import PortalRouteBinding
from portal.route_resolver import (
    PortalRouteAdvertisement,
    PortalRouteRequest,
    resolve_portal_route,
)


def _route(
    *,
    adapter_id: str,
    route_id: str,
    node_id: str = "repo-native",
    target_kind: str = "repository",
    target_id: str = "thebrazenbeard/portal",
    capabilities: tuple[str, ...] = ("repository_read",),
    effect_capabilities: tuple[str, ...] = ("NO_PROTECTED_EFFECT",),
    authorized_effects: tuple[str, ...] = ("NO_PROTECTED_EFFECT",),
    available: bool = True,
    attached: bool = True,
    current: bool = True,
    preference: int = 0,
) -> PortalRouteAdvertisement:
    return PortalRouteAdvertisement(
        adapter_id=adapter_id,
        route_id=route_id,
        node_id=node_id,
        target_kind=target_kind,
        target_id=target_id,
        capabilities=capabilities,
        effect_capabilities=effect_capabilities,
        authorized_effects=authorized_effects,
        available=available,
        attached=attached,
        current=current,
        preference=preference,
    )


def _request(**overrides) -> PortalRouteRequest:
    values = dict(
        subject_kind="repository",
        subject_id="portal",
        node_id="repo-native",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        required_capabilities=("repository_read",),
        required_effect="NO_PROTECTED_EFFECT",
    )
    values.update(overrides)
    return PortalRouteRequest(**values)


def test_resolver_prefers_host_policy_without_hardcoding_adapter_kind() -> None:
    selected = resolve_portal_route(
        _request(),
        (
            _route(
                adapter_id="workbridge",
                route_id="repo-through-workbridge",
                preference=10,
            ),
            _route(
                adapter_id="github",
                route_id="repo-native",
                preference=50,
            ),
        ),
    )

    assert selected == PortalRouteBinding(
        subject_kind="repository",
        subject_id="portal",
        adapter_id="github",
        route_id="repo-native",
    )


def test_availability_attachment_currentness_capability_and_authority_are_distinct() -> None:
    selected = resolve_portal_route(
        _request(
            required_capabilities=("repository_read", "repository_write"),
            required_effect="SOURCE_WRITE",
        ),
        (
            _route(
                adapter_id="github",
                route_id="available-not-attached",
                capabilities=("repository_read", "repository_write"),
                effect_capabilities=("SOURCE_WRITE",),
                authorized_effects=("SOURCE_WRITE",),
                attached=False,
                preference=100,
            ),
            _route(
                adapter_id="workbridge",
                route_id="attached-stale",
                capabilities=("repository_read", "repository_write"),
                effect_capabilities=("SOURCE_WRITE",),
                authorized_effects=("SOURCE_WRITE",),
                current=False,
                preference=90,
            ),
            _route(
                adapter_id="executor",
                route_id="technically-capable-not-authorized",
                capabilities=("repository_read", "repository_write"),
                effect_capabilities=("SOURCE_WRITE",),
                authorized_effects=("NO_PROTECTED_EFFECT",),
                preference=80,
            ),
            _route(
                adapter_id="repo-plugin",
                route_id="qualified-write",
                capabilities=("repository_read", "repository_write"),
                effect_capabilities=("SOURCE_WRITE",),
                authorized_effects=("SOURCE_WRITE",),
                preference=10,
            ),
        ),
    )

    assert selected.adapter_id == "repo-plugin"
    assert selected.route_id == "qualified-write"


def test_route_is_bound_to_assigned_node_and_exact_target() -> None:
    selected = resolve_portal_route(
        _request(node_id="worklaptop"),
        (
            _route(
                adapter_id="github",
                route_id="wrong-node",
                node_id="repo-native",
                preference=100,
            ),
            _route(
                adapter_id="executor",
                route_id="wrong-repo",
                node_id="worklaptop",
                target_id="thebrazenbeard/tattler",
                preference=90,
            ),
            _route(
                adapter_id="workbridge",
                route_id="portal-on-worklaptop",
                node_id="worklaptop",
                preference=1,
            ),
        ),
    )

    assert selected.adapter_id == "workbridge"
    assert selected.route_id == "portal-on-worklaptop"


def test_explicit_route_selection_fails_closed_without_fallback() -> None:
    request = _request(
        preferred_adapter_id="workbridge",
        preferred_route_id="repo-through-workbridge",
    )

    with pytest.raises(ValueError, match="explicit route is not currently qualified"):
        resolve_portal_route(
            request,
            (
                _route(
                    adapter_id="workbridge",
                    route_id="repo-through-workbridge",
                    current=False,
                ),
                _route(
                    adapter_id="github",
                    route_id="repo-native",
                    preference=100,
                ),
            ),
        )


def test_missing_required_capability_fails_closed() -> None:
    with pytest.raises(ValueError, match="no qualified execution route"):
        resolve_portal_route(
            _request(required_capabilities=("repository_read", "process_execute")),
            (
                _route(
                    adapter_id="github",
                    route_id="repo-native",
                    capabilities=("repository_read",),
                ),
            ),
        )


def test_technical_effect_capability_does_not_create_effect_authority() -> None:
    with pytest.raises(ValueError, match="no qualified execution route"):
        resolve_portal_route(
            _request(required_effect="SOURCE_WRITE"),
            (
                _route(
                    adapter_id="github",
                    route_id="write-token",
                    effect_capabilities=("SOURCE_WRITE",),
                    authorized_effects=("NO_PROTECTED_EFFECT",),
                ),
            ),
        )


def test_equal_preference_selection_is_deterministic() -> None:
    selected = resolve_portal_route(
        _request(),
        (
            _route(
                adapter_id="workbridge",
                route_id="z-route",
                preference=5,
            ),
            _route(
                adapter_id="executor",
                route_id="b-route",
                preference=5,
            ),
            _route(
                adapter_id="executor",
                route_id="a-route",
                preference=5,
            ),
        ),
    )

    assert selected.adapter_id == "executor"
    assert selected.route_id == "a-route"


def test_preferred_adapter_and_route_must_be_supplied_together() -> None:
    with pytest.raises(ValueError, match="preferred adapter and route must be paired"):
        _request(preferred_adapter_id="github")

def test_same_physical_route_can_advertise_multiple_exact_targets() -> None:
    selected = resolve_portal_route(
        _request(
            preferred_adapter_id="github",
            preferred_route_id="repo-native",
        ),
        (
            _route(
                adapter_id="github",
                route_id="repo-native",
                target_id="thebrazenbeard/portal",
            ),
            _route(
                adapter_id="github",
                route_id="repo-native",
                target_id="thebrazenbeard/tattler",
            ),
        ),
    )

    assert selected == PortalRouteBinding(
        subject_kind="repository",
        subject_id="portal",
        adapter_id="github",
        route_id="repo-native",
    )

