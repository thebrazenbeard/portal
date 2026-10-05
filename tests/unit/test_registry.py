import hashlib
from pathlib import Path
import pytest
from runner.registry import load_project_snapshot, load_workers, load_projects


def test_duplicate_worker_ids_are_rejected(tmp_path: Path):
    path = tmp_path / "workers.yaml"
    path.write_text("""
workers:
  - id: same
    name: One
    worker_type: HUMAN
    lifecycle: REGISTERED
    locators: {handle: one}
    roles: []
    routes: {}
  - id: same
    name: Two
    worker_type: HUMAN
    lifecycle: REGISTERED
    locators: {handle: two}
    roles: []
    routes: {}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate worker id: same"):
        load_workers(path)


def test_duplicate_project_ids_are_rejected(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    path.write_text("""
projects:
  - id: same
    name: One
    visibility: public
    repositories: [owner/one]
    capabilities: [read]
  - id: same
    name: Two
    visibility: public
    repositories: [owner/two]
    capabilities: [read]
""", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate project id: same"):
        load_projects(path)


def test_project_portfolio_axes_are_parsed(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    path.write_text("""
projects:
  - id: example
    name: Example
    visibility: private
    repositories: [owner/example]
    capabilities: [read, analyze, propose]
    assignment_scope: BT2_ASSIGNMENT
    review_scope: STANDING
    family_id: example-family
    scheduling_state: HELD
    scope_note: explicit test membership
""", encoding="utf-8")
    project = load_projects(path)[0]
    assert project.assignment_scope.value == "BT2_ASSIGNMENT"
    assert project.review_scope.value == "STANDING"
    assert project.family_id == "example-family"
    assert project.scheduling_state.value == "HELD"
    assert project.scope_note == "explicit test membership"


def test_project_registry_snapshot_binds_exact_bytes(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    first = b"""projects:
  - id: example
    name: Example
    visibility: private
    repositories: [owner/example]
    capabilities: [read]
    assignment_scope: EXTERNAL_BOUNDED
    review_scope: STANDING
    family_id: example-family
    scheduling_state: SCHEDULABLE
"""
    path.write_bytes(first)

    snapshot = load_project_snapshot(path, require_scope_metadata=True)

    assert snapshot.sha256 == hashlib.sha256(first).hexdigest()
    assert snapshot.byte_length == len(first)
    assert tuple(project.id for project in snapshot.projects) == ("example",)

    second = first.replace(b"name: Example", b"name: Example Changed")
    path.write_bytes(second)
    changed = load_project_snapshot(path, require_scope_metadata=True)

    assert changed.sha256 == hashlib.sha256(second).hexdigest()
    assert changed.sha256 != snapshot.sha256
    assert changed.projects[0].name == "Example Changed"


def test_external_scope_metadata_requires_explicit_scheduling_state(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    raw = b"""projects:
  - id: held-example
    name: Held Example
    visibility: private
    repositories: [owner/held-example]
    capabilities: [read, analyze]
    assignment_scope: EXTERNAL_BOUNDED
    review_scope: STANDING
    family_id: held-family
"""
    path.write_bytes(raw)

    with pytest.raises(ValueError, match="scheduling_state"):
        load_project_snapshot(path, require_scope_metadata=True)


@pytest.mark.parametrize(
    "state",
    ["HELD", "ARCHIVED", "DORMANT", "SENSITIVE_HELD", "DECISION_HELD"],
)
def test_non_schedulable_project_states_remain_explicit(tmp_path: Path, state: str):
    path = tmp_path / "projects.yaml"
    path.write_text(
        f"""projects:
  - id: example
    name: Example
    visibility: private
    repositories: [owner/example]
    capabilities: [read, analyze]
    assignment_scope: EXTERNAL_BOUNDED
    review_scope: STANDING
    family_id: example-family
    scheduling_state: {state}
""",
        encoding="utf-8",
    )
    project = load_project_snapshot(path, require_scope_metadata=True).projects[0]
    assert project.scheduling_state.value == state
    assert project.scheduling_state.schedulable is False



def test_project_execution_target_is_parsed_and_bound_to_project_repository(
    tmp_path: Path,
):
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
projects:
  - id: example
    name: Example
    visibility: public
    repositories: [owner/example]
    capabilities: [read, analyze]
    execution_targets:
      - work_type: INSPECT
        repository: owner/example
        ref: review
""".lstrip(),
        encoding="utf-8",
    )
    project = load_projects(path)[0]
    assert len(project.execution_targets) == 1
    assert project.execution_targets[0].work_type == "INSPECT"
    assert project.execution_targets[0].repository == "owner/example"
    assert project.execution_targets[0].ref == "review"


def test_project_execution_target_cannot_escape_project_repository_scope(
    tmp_path: Path,
):
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
projects:
  - id: example
    name: Example
    visibility: public
    repositories: [owner/example]
    capabilities: [read, analyze]
    execution_targets:
      - work_type: INSPECT
        repository: attacker/other
        ref: main
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="execution target repository must belong",
    ):
        load_projects(path)


def test_project_execution_target_work_type_must_be_unique(tmp_path: Path):
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
projects:
  - id: example
    name: Example
    visibility: public
    repositories: [owner/example]
    capabilities: [read, analyze]
    execution_targets:
      - work_type: INSPECT
        repository: owner/example
        ref: main
      - work_type: INSPECT
        repository: owner/example
        ref: review
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="work_type must be unique",
    ):
        load_projects(path)



def test_worker_route_contract_is_bound_to_declared_route(tmp_path: Path):
    path = tmp_path / "workers.yaml"
    path.write_text(
        """
workers:
  - id: reviewer
    name: Reviewer
    worker_type: OPENAI_AGENT
    lifecycle: EXECUTABLE
    locators: {model: reviewer-model}
    roles: [review]
    routes:
      OPENAI_AGENT_API: VERIFIED
    route_contracts:
      OPENAI_AGENT_API:
        effect_class: READ_ONLY
        replay_policy: SAFE
    reconstruction:
      repository: owner/workers
      path: workers/reviewer.md
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""".lstrip(),
        encoding="utf-8",
    )
    worker = load_workers(path)[0]
    contract = worker.route_contracts[next(iter(worker.route_contracts))]
    assert contract.effect_class.value == "READ_ONLY"
    assert contract.replay_policy.value == "SAFE"


def test_worker_route_contract_cannot_name_undeclared_route(tmp_path: Path):
    path = tmp_path / "workers.yaml"
    path.write_text(
        """
workers:
  - id: reviewer
    name: Reviewer
    worker_type: OPENAI_AGENT
    lifecycle: REGISTERED
    locators: {model: reviewer-model}
    roles: [review]
    routes:
      OPENAI_AGENT_API: UNVERIFIED
    route_contracts:
      GITHUB_ACTION:
        effect_class: READ_ONLY
        replay_policy: SAFE
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires a declared worker route"):
        load_workers(path)
