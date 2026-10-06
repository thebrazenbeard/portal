from __future__ import annotations

from pathlib import Path

import pytest

from portal.host_authority import apply_host_authority, load_host_authority
from portal.route_resolver import PortalRouteAdvertisement


def _route() -> PortalRouteAdvertisement:
    return PortalRouteAdvertisement(
        adapter_id="workbridge",
        route_id="lappy:portal",
        node_id="lappy",
        target_kind="repository",
        target_id="thebrazenbeard/portal",
        capabilities=("semantic_work",),
        effect_capabilities=("SOURCE_ONLY",),
        authorized_effects=(),
        available=True,
        attached=True,
        current=True,
        preference=20,
    )


def test_authority_manifest_applies_only_exact_route_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authority.yaml"
    path.write_text(
        "schema: PORTAL_HOST_AUTHORITY_V1\n"
        "grants:\n"
        "  - adapter_id: workbridge\n"
        "    route_id: lappy:portal\n"
        "    node_id: lappy\n"
        "    target_kind: repository\n"
        "    target_id: thebrazenbeard/portal\n"
        "    authorized_effects: [SOURCE_ONLY]\n",
        encoding="utf-8",
    )

    grants = load_host_authority(path)
    applied = apply_host_authority((_route(),), grants)

    assert applied[0].authorized_effects == ("SOURCE_ONLY",)


def test_authority_grant_cannot_exceed_observed_effect_capability(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authority.yaml"
    path.write_text(
        "schema: PORTAL_HOST_AUTHORITY_V1\n"
        "grants:\n"
        "  - adapter_id: workbridge\n"
        "    route_id: lappy:portal\n"
        "    node_id: lappy\n"
        "    target_kind: repository\n"
        "    target_id: thebrazenbeard/portal\n"
        "    authorized_effects: [PROTECTED_EFFECT]\n",
        encoding="utf-8",
    )

    grants = load_host_authority(path)
    with pytest.raises(
        ValueError,
        match="exceeds observed effect capability",
    ):
        apply_host_authority((_route(),), grants)


def test_unmatched_authority_grant_does_not_create_route(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authority.yaml"
    path.write_text(
        "schema: PORTAL_HOST_AUTHORITY_V1\n"
        "grants:\n"
        "  - adapter_id: github\n"
        "    route_id: repo-native:thebrazenbeard/portal\n"
        "    node_id: repo-native\n"
        "    target_kind: repository\n"
        "    target_id: thebrazenbeard/portal\n"
        "    authorized_effects: [SOURCE_ONLY]\n",
        encoding="utf-8",
    )

    grants = load_host_authority(path)
    applied = apply_host_authority((_route(),), grants)

    assert len(applied) == 1
    assert applied[0].adapter_id == "workbridge"
    assert applied[0].authorized_effects == ()
