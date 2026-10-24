from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from portal.discovery import (
    RepositoryInventoryItem,
    build_live_project_registry,
    write_project_registry,
)
from runner.models import ProjectDefinition
from runner.registry import load_project_snapshot


def _repo(
    name: str,
    *,
    private: bool = False,
    archived: bool = False,
    default_branch: str = "main",
) -> RepositoryInventoryItem:
    return RepositoryInventoryItem(
        name=name,
        full_name=f"thebrazenbeard/{name}",
        private=private,
        archived=archived,
        default_branch=default_branch,
    )


def _curated() -> ProjectDefinition:
    return ProjectDefinition.from_mapping(
        {
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": ["thebrazenbeard/project-runner"],
            "capabilities": ["read", "analyze", "propose"],
            "assignment_scope": "EXTERNAL_BOUNDED",
            "review_scope": "STANDING",
            "family_id": "portfolio-spine",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "INSPECT",
                    "repository": "thebrazenbeard/project-runner",
                    "ref": "main",
                }
            ],
            "scope_note": "curated metadata",
        }
    )


def test_live_registry_covers_discovered_estate_and_preserves_exact_curated_metadata():
    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            _repo("project-runner"),
            _repo("secret-lab", private=True),
            _repo("old-lab", archived=True),
        ),
        curated_projects=(_curated(),),
    )

    assert len(snapshot.projects) == 3
    projects = {project.id: project for project in snapshot.projects}

    runner = projects["project-runner"]
    assert runner.family_id == "portfolio-spine"
    assert runner.assignment_scope.value == "EXTERNAL_BOUNDED"
    assert runner.review_scope.value == "STANDING"
    assert runner.execution_targets[0].repository == "thebrazenbeard/project-runner"

    secret = projects["secret-lab"]
    assert secret.visibility == "private"
    assert secret.scheduling_state.value == "SCHEDULABLE"
    assert secret.assignment_scope.value == "NONE"
    assert secret.review_scope.value == "NONE"
    assert secret.execution_targets == ()

    archived = projects["old-lab"]
    assert archived.scheduling_state.value == "ARCHIVED"

    assert len(snapshot.sha256) == 64
    assert snapshot.byte_length > 0


def test_live_registry_uses_live_visibility_and_archive_state_over_stale_curated_values():
    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            RepositoryInventoryItem(
                name="project-runner",
                full_name="thebrazenbeard/project-runner",
                private=True,
                archived=True,
                default_branch="main",
            ),
        ),
        curated_projects=(_curated(),),
    )

    project = snapshot.projects[0]
    assert project.visibility == "private"
    assert project.scheduling_state.value == "ARCHIVED"
    assert project.family_id == "portfolio-spine"


def test_live_registry_rejects_duplicate_repository_identity():
    duplicate = _repo("same")
    with pytest.raises(ValueError, match="duplicate discovered repository"):
        build_live_project_registry(
            owner="thebrazenbeard",
            repositories=(duplicate, duplicate),
            curated_projects=(),
        )


def test_live_registry_duplicate_private_identity_is_redacted():
    private_name = "synthetic-private-repository"
    duplicate = _repo(private_name, private=True)
    with pytest.raises(ValueError, match="duplicate discovered repository") as error:
        build_live_project_registry(
            owner="thebrazenbeard",
            repositories=(duplicate, duplicate),
        )
    assert private_name not in str(error.value)


def test_live_registry_ambiguous_private_binding_is_redacted():
    private_name = "synthetic-private-ambiguous"
    one = ProjectDefinition.from_mapping(
        {
            "id": "one", "name": "one", "visibility": "private",
            "repositories": [f"thebrazenbeard/{private_name}"],
            "capabilities": ["read"], "assignment_scope": "NONE",
            "review_scope": "NONE", "family_id": "one",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    two = ProjectDefinition.from_mapping(
        {
            "id": "two", "name": "two", "visibility": "private",
            "repositories": [f"thebrazenbeard/{private_name}"],
            "capabilities": ["read"], "assignment_scope": "NONE",
            "review_scope": "NONE", "family_id": "two",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    with pytest.raises(ValueError, match="curated repository binding is ambiguous") as error:
        build_live_project_registry(
            owner="thebrazenbeard",
            repositories=(_repo(private_name, private=True),),
            curated_projects=(one, two),
        )
    assert private_name not in str(error.value)


def test_live_registry_duplicate_private_project_id_is_redacted():
    private_name = "synthetic-private-project-id"
    first = _repo(private_name, private=True)
    second = RepositoryInventoryItem(
        name=private_name,
        full_name="thebrazenbeard/synthetic-other-private-repository",
        private=True,
        archived=False,
        default_branch="main",
    )
    with pytest.raises(ValueError, match="duplicate live project id") as error:
        build_live_project_registry(
            owner="thebrazenbeard",
            repositories=(first, second),
        )
    assert private_name not in str(error.value)


def test_live_registry_rejects_curated_repository_ambiguity():
    first = ProjectDefinition.from_mapping(
        {
            "id": "one",
            "name": "one",
            "visibility": "public",
            "repositories": ["thebrazenbeard/shared"],
            "capabilities": ["read"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "one",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    second = ProjectDefinition.from_mapping(
        {
            "id": "two",
            "name": "two",
            "visibility": "public",
            "repositories": ["thebrazenbeard/shared"],
            "capabilities": ["read"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "two",
            "scheduling_state": "SCHEDULABLE",
        }
    )

    with pytest.raises(ValueError, match="curated repository binding is ambiguous"):
        build_live_project_registry(
            owner="thebrazenbeard",
            repositories=(_repo("shared"),),
            curated_projects=(first, second),
        )


def test_written_live_registry_is_valid_external_project_runner_input(
    tmp_path: Path,
):
    snapshot = build_live_project_registry(
        owner="thebrazenbeard",
        repositories=(
            _repo("project-runner"),
            _repo("new-project"),
        ),
        curated_projects=(_curated(),),
    )
    path = tmp_path / "projects.live.yaml"
    write_project_registry(path, snapshot)

    loaded = load_project_snapshot(path, require_scope_metadata=True)
    assert [project.id for project in loaded.projects] == [
        "new-project",
        "project-runner",
    ]
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
