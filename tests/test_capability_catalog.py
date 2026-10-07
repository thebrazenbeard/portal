from __future__ import annotations

import json
from pathlib import Path

import pytest

from portal.capability_catalog import (
    CapabilityCatalogError,
    JsonCapabilityCatalog,
)


def write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "VERA_OS_PRIVATE_PORTFOLIO_CAPABILITY_MAP_V1",
                "visibility": "PRIVATE_VERAOS_INTERNAL",
                "policy": {
                    "allowed_owner_dispositions": [
                        "REUSE",
                        "EXTEND",
                        "SUPERSEDE",
                        "REJECT",
                    ]
                },
                "capabilities": {
                    "reasoning_architecture": {
                        "owners": ["thebrazenbeard/rezon"],
                        "supporting": ["thebrazenbeard/voss"],
                        "role": "Heterogeneous reasoning architecture.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_external_private_catalog_can_be_loaded_without_embedding_it(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_catalog(path)

    catalog = JsonCapabilityCatalog(path)
    match = catalog.lookup("reasoning_architecture")

    assert match.owners == ("thebrazenbeard/rezon",)
    assert match.supporting == ("thebrazenbeard/voss",)


def test_known_owners_must_be_disposed_before_assignment(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_catalog(path)
    catalog = JsonCapabilityCatalog(path)

    with pytest.raises(CapabilityCatalogError, match="thebrazenbeard/rezon"):
        catalog.validate_assignment(
            capability="reasoning_architecture",
            dispositions=(),
        )


def test_reuse_disposition_satisfies_gate(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_catalog(path)
    catalog = JsonCapabilityCatalog(path)

    catalog.validate_assignment(
        capability="reasoning_architecture",
        dispositions=(
            {
                "repository": "thebrazenbeard/rezon",
                "disposition": "REUSE",
                "evidence": "Rezon is the current reasoning architecture.",
            },
        ),
    )


def test_unknown_capability_fails_closed_to_discovery(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_catalog(path)
    catalog = JsonCapabilityCatalog(path)

    with pytest.raises(CapabilityCatalogError, match="Discovery"):
        catalog.lookup("invented_new_brain")


def test_catalog_path_is_runtime_input_not_repository_data(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_catalog(path)
    catalog = JsonCapabilityCatalog(path)

    assert catalog.path == path
