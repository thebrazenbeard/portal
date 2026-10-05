import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from runner import cli as cli_module
from runner.cli import main


def _pin_external_registry(registry, monkeypatch):
    monkeypatch.setenv(
        "PROJECT_RUNNER_PROJECT_REGISTRY",
        str(registry.resolve()),
    )
    monkeypatch.setenv(
        "PROJECT_RUNNER_PROJECT_REGISTRY_SHA256",
        hashlib.sha256(registry.read_bytes()).hexdigest(),
    )
    monkeypatch.setenv(
        "PROJECT_RUNNER_PRIVATE_COLLISION_KEY",
        "11" * 32,
    )


def test_validate_command_returns_zero(capsys):
    assert main(["validate"]) == 0
    assert "registries valid" in capsys.readouterr().out.lower()


def test_inventory_reports_reference_worker_and_twelve_registered_workers(capsys):
    assert main(["inventory"]) == 0
    out = capsys.readouterr().out
    assert "projects: 15" in out.lower()
    assert "workers: 13" in out.lower()
    assert "registered: 12" in out.lower()
    assert "executable: 1" in out.lower()


def test_inventory_uses_explicit_external_project_registry(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "private-projects.yaml"
    registry.write_text(
        """
projects:
- id: private-example
  name: Private Example
  visibility: private
  repositories:
  - example-owner/private-example
  capabilities:
  - read
  - analyze
  - propose
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    assert main(["inventory"]) == 0
    out = capsys.readouterr().out.lower()
    assert "projects: 1" in out
    assert "workers: 13" in out


def test_external_registry_requires_explicit_scope_metadata(tmp_path, monkeypatch):
    registry = tmp_path / "secret-portfolio.yaml"
    registry.write_text(
        """
projects:
- id: private-project-name
  name: Private Project Name
  visibility: private
  repositories:
  - private-owner/private-repository
  capabilities:
  - read
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    with pytest.raises(ValueError, match="requires explicit assignment_scope") as exc:
        main(["inventory"])

    message = str(exc.value)
    assert "private-project-name" not in message
    assert "private-owner" not in message


def test_external_registry_missing_path_is_redacted(tmp_path, monkeypatch):
    secret_path = tmp_path / "private-client-project.yaml"
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", str(secret_path.resolve()))
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY_SHA256", "a" * 64)

    with pytest.raises(ValueError, match="external project registry is unavailable") as exc:
        main(["inventory"])

    assert "private-client-project" not in str(exc.value)


def test_external_registry_rejects_relative_path(monkeypatch):
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", "private/projects.yaml")
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY_SHA256", "a" * 64)

    with pytest.raises(ValueError, match="requires an absolute path"):
        main(["inventory"])


def test_external_registry_must_live_outside_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    registry = checkout / "private-projects.yaml"
    registry.write_text(
        """
projects:
- id: example
  name: Example
  visibility: private
  repositories: [owner/example]
  capabilities: [read]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: example-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "ROOT", checkout)
    _pin_external_registry(registry, monkeypatch)

    with pytest.raises(ValueError, match="outside the Project Runner checkout"):
        main(["inventory"])


def test_external_registry_requires_expected_digest(tmp_path, monkeypatch):
    registry = tmp_path / "private-client-alpha.yaml"
    registry.write_text(
        """
projects:
- id: private-client-alpha
  name: Private Client Alpha
  visibility: private
  repositories: [secret-owner/secret-repository]
  capabilities: [read]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-alpha
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", str(registry.resolve()))

    with pytest.raises(ValueError, match="requires an expected lowercase SHA-256") as exc:
        main(["inventory"])

    message = str(exc.value)
    assert "private-client-alpha" not in message
    assert "secret-owner" not in message


def test_external_registry_digest_mismatch_fails_closed_and_redacted(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-client-beta.yaml"
    registry.write_text(
        """
projects:
- id: private-client-beta
  name: Private Client Beta
  visibility: private
  repositories: [secret-owner/secret-beta]
  capabilities: [read]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-beta
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY", str(registry.resolve()))
    monkeypatch.setenv("PROJECT_RUNNER_PROJECT_REGISTRY_SHA256", "0" * 64)

    with pytest.raises(ValueError, match="external project registry digest mismatch") as exc:
        main(["inventory"])

    message = str(exc.value)
    assert "private-client-beta" not in message
    assert "secret-owner" not in message
    assert "private-client-beta.yaml" not in message


@pytest.mark.parametrize(
    "command",
    ["evaluate-change", "frontier-report", "dispatch-report"],
)
def test_external_registry_disables_detailed_reporting_before_private_inputs_load(
    command,
    tmp_path,
    monkeypatch,
    capsys,
):
    registry = tmp_path / "private-registry.yaml"
    registry.write_text(
        """
projects:
- id: private-example
  name: Private Example
  visibility: private
  repositories: [secret-owner/private-example]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    secret_before = tmp_path / "secret-before-do-not-read.yaml"
    secret_after = tmp_path / "secret-after-do-not-read.yaml"
    secret_dependencies = tmp_path / "secret-dependencies-do-not-read.yaml"

    with pytest.raises(
        ValueError,
        match="detailed reports are disabled with an external project registry",
    ):
        main(
            [
                command,
                "--before",
                str(secret_before),
                "--after",
                str(secret_after),
                "--dependencies",
                str(secret_dependencies),
            ]
        )

    assert capsys.readouterr().out == ""


def test_external_held_project_cannot_become_ready_frontier(tmp_path, monkeypatch):
    registry = tmp_path / "private-held.yaml"
    registry.write_text(
        """
projects:
- id: transcendence
  name: Held Transcendence
  visibility: private
  repositories: [secret-owner/private-transcendence]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: transcendence-family
  scheduling_state: HELD
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    frontiers = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )

    assert len(frontiers) == 1
    assert frontiers[0].project == "transcendence"
    assert frontiers[0].status.value == "WAITING_SCHEDULING"


def test_external_registry_obscures_collision_keys(tmp_path, monkeypatch):
    registry = tmp_path / "private-schedulable.yaml"
    registry.write_text(
        """
projects:
- id: transcendence
  name: Private Transcendence
  visibility: private
  repositories: [secret-owner/private-transcendence]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: transcendence-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    first = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )
    second = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )

    assert len(first) == len(second) == 1
    assert first[0].status.value == "READY"
    assert first[0].collision_keys == second[0].collision_keys
    assert len(first[0].collision_keys) == 1
    collision_key = first[0].collision_keys[0]
    assert collision_key.startswith("private:")
    assert "transcendence" not in collision_key
    assert "secret-owner" not in collision_key
    assert "project:" not in collision_key


def test_external_registry_requires_secret_collision_key_for_frontier_derivation(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-collision-key.yaml"
    registry.write_text(
        """
projects:
- id: transcendence
  name: Private Transcendence
  visibility: private
  repositories: [secret-owner/private-transcendence]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: transcendence-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "PROJECT_RUNNER_PROJECT_REGISTRY",
        str(registry.resolve()),
    )
    monkeypatch.setenv(
        "PROJECT_RUNNER_PROJECT_REGISTRY_SHA256",
        hashlib.sha256(registry.read_bytes()).hexdigest(),
    )
    monkeypatch.delenv("PROJECT_RUNNER_PRIVATE_COLLISION_KEY", raising=False)

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    with pytest.raises(
        ValueError,
        match="requires a private collision key",
    ):
        cli_module._derive_frontier_set(
            fixtures / "m6-hc-substantive.yaml",
            fixtures / "m6-hc-current.yaml",
            fixtures / "m6-hc-transcendence-dependencies.yaml",
        )


def test_private_collision_key_is_secret_keyed_not_registry_digest(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-collision-rotation.yaml"
    registry.write_text(
        """
projects:
- id: transcendence
  name: Private Transcendence
  visibility: private
  repositories: [secret-owner/private-transcendence]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: transcendence-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"

    first = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )
    monkeypatch.setenv("PROJECT_RUNNER_PRIVATE_COLLISION_KEY", "22" * 32)
    second = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )

    assert first[0].collision_keys != second[0].collision_keys
    for collision_key in first[0].collision_keys + second[0].collision_keys:
        assert collision_key.startswith("private:")
        assert "transcendence" not in collision_key
        assert "secret-owner" not in collision_key


def test_external_registry_frontier_summary_exposes_counts_not_private_identity(
    tmp_path,
    monkeypatch,
    capsys,
):
    registry = tmp_path / "private-summary.yaml"
    registry.write_text(
        """
projects:
- id: transcendence
  name: Private Transcendence
  visibility: private
  repositories: [secret-owner/private-transcendence]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: transcendence-family
  scheduling_state: HELD
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"

    assert main(
        [
            "frontier-summary",
            "--before",
            str(fixtures / "m6-hc-substantive.yaml"),
            "--after",
            str(fixtures / "m6-hc-current.yaml"),
            "--dependencies",
            str(fixtures / "m6-hc-transcendence-dependencies.yaml"),
        ]
    ) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload == {
        "blocked": 1,
        "ready": 0,
        "statuses": {"WAITING_SCHEDULING": 1},
        "total": 1,
    }
    assert "transcendence" not in output
    assert "secret-owner" not in output
    assert "private-summary" not in output
    assert "collision" not in output


def test_external_registry_frontier_summary_redacts_input_failure(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-summary.yaml"
    registry.write_text(
        """
projects:
- id: private-example
  name: Private Example
  visibility: private
  repositories: [secret-owner/private-example]
  capabilities: [read]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    secret_before = tmp_path / "do-not-leak-private-before.yaml"
    secret_after = tmp_path / "do-not-leak-private-after.yaml"
    secret_dependencies = tmp_path / "do-not-leak-private-dependencies.yaml"

    with pytest.raises(
        ValueError,
        match="external frontier summary is unavailable or structurally invalid",
    ) as exc:
        main(
            [
                "frontier-summary",
                "--before",
                str(secret_before),
                "--after",
                str(secret_after),
                "--dependencies",
                str(secret_dependencies),
            ]
        )

    message = str(exc.value)
    assert "do-not-leak" not in message
    assert "private-example" not in message
    assert "secret-owner" not in message


@pytest.mark.parametrize(
    "state",
    ["HELD", "ARCHIVED", "DORMANT", "SENSITIVE_HELD", "DECISION_HELD"],
)
def test_external_non_schedulable_states_never_become_ready(
    state,
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-held-state.yaml"
    registry.write_text(
        f"""projects:
- id: transcendence
  name: Held Project
  visibility: private
  repositories: [secret-owner/private-project]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: held-family
  scheduling_state: {state}
""",
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    frontiers = cli_module._derive_frontier_set(
        fixtures / "m6-hc-substantive.yaml",
        fixtures / "m6-hc-current.yaml",
        fixtures / "m6-hc-transcendence-dependencies.yaml",
    )
    assert len(frontiers) == 1
    assert frontiers[0].status.value == "WAITING_SCHEDULING"


def test_operator_status_missing_database_is_safe_and_empty(tmp_path, capsys):
    state_db = tmp_path / "missing-state.sqlite3"

    assert main(
        [
            "operator-status",
            "--state-db",
            str(state_db),
        ]
    ) == 0

    assert json.loads(capsys.readouterr().out) == {
        "actions": {},
        "live_fences": 0,
        "phases": {},
        "unresolved": 0,
    }


def test_external_registry_disables_detailed_operator_status(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-operator.yaml"
    registry.write_text(
        """
projects:
- id: private-example
  name: Private Example
  visibility: private
  repositories: [secret-owner/private-example]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)

    with pytest.raises(
        ValueError,
        match="detailed reports are disabled with an external project registry",
    ):
        main(
            [
                "operator-status",
                "--state-db",
                str(tmp_path / "state.sqlite3"),
                "--detailed",
            ]
        )



def test_portfolio_status_missing_database_is_safe_and_empty(tmp_path, capsys):
    state_db = tmp_path / "missing-portfolio.sqlite3"

    assert main(
        [
            "portfolio-status",
            "--state-db",
            str(state_db),
        ]
    ) == 0

    assert json.loads(capsys.readouterr().out) == {
        "baseline": None,
        "blocked": 0,
        "changed": 0,
        "frontiers": 0,
        "latest_snapshot_digest": None,
        "latest_snapshot_id": None,
        "observations": 0,
        "queued_total": 0,
        "ready": 0,
        "snapshots": 0,
    }


def test_portfolio_cycle_collects_then_schedules_changed_dependency(
    tmp_path,
    monkeypatch,
    capsys,
):
    from runner import portfolio as portfolio_module

    class FakeTransport:
        def __init__(self):
            self.heads = {
                ("thebrazenbeard/chat-communication-bus", "main"): "a" * 40,
                ("thebrazenbeard/vera-control-plane", "main"): "b" * 40,
            }

        def read_ref(self, repository, ref):
            return self.heads[(repository, ref)]

        def read_file(self, repository, path, ref):
            raise AssertionError("portfolio cycle is ref-read only")

        def create_branch(self, repository, branch, sha):
            raise AssertionError("portfolio cycle is read-only")

        def put_file(
            self,
            repository,
            path,
            branch,
            content,
            message,
            expected_blob_sha=None,
        ):
            raise AssertionError("portfolio cycle is read-only")

    transport = FakeTransport()
    monkeypatch.setattr(
        portfolio_module,
        "GitHubRestTransport",
        lambda token=None: transport,
    )
    state_db = tmp_path / "portfolio.sqlite3"

    assert main(
        [
            "portfolio-cycle",
            "--state-db",
            str(state_db),
        ]
    ) == 0
    baseline = json.loads(capsys.readouterr().out)
    assert baseline["baseline"] is True
    assert baseline["observations"] == 2
    assert baseline["frontiers"] == 0
    assert baseline["queued"] == 0

    transport.heads[
        ("thebrazenbeard/chat-communication-bus", "main")
    ] = "c" * 40

    assert main(
        [
            "portfolio-cycle",
            "--state-db",
            str(state_db),
        ]
    ) == 0
    changed = json.loads(capsys.readouterr().out)
    assert changed["baseline"] is False
    assert changed["changed"] == 1
    assert changed["frontiers"] == 1
    assert changed["ready"] == 1
    assert changed["queued"] == 1

    assert main(
        [
            "portfolio-status",
            "--state-db",
            str(state_db),
        ]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["snapshots"] == 2
    assert status["queued_total"] == 1



def test_external_portfolio_cycle_redacts_private_dependency_failure(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-portfolio.yaml"
    registry.write_text(
        """
projects:
- id: private-provider
  name: Private Provider
  visibility: private
  repositories: [secret-owner/private-provider]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-provider-family
  scheduling_state: SCHEDULABLE
- id: private-consumer
  name: Private Consumer
  visibility: private
  repositories: [secret-owner/private-consumer]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-consumer-family
  scheduling_state: SCHEDULABLE
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    private_dependencies = tmp_path / "do-not-leak-client-topology.yaml"

    with pytest.raises(
        ValueError,
        match="external portfolio cycle is unavailable or structurally invalid",
    ) as exc:
        main(
            [
                "portfolio-cycle",
                "--dependencies",
                str(private_dependencies),
                "--state-db",
                str(tmp_path / "state.sqlite3"),
            ]
        )

    message = str(exc.value)
    assert "do-not-leak" not in message
    assert "private-provider" not in message
    assert "private-consumer" not in message
    assert "secret-owner" not in message



def test_cli_portfolio_to_queue_consumption_round_trip(
    tmp_path,
    monkeypatch,
    capsys,
):
    from runner import portfolio as portfolio_module
    from runner import queue_consumer as queue_module

    class FakeTransport:
        def __init__(self):
            self.heads = {
                ("thebrazenbeard/chat-communication-bus", "main"): "a" * 40,
                ("thebrazenbeard/vera-control-plane", "main"): "b" * 40,
                ("thebrazenbeard/project-runner", "main"): "c" * 40,
            }

        def read_ref(self, repository, ref):
            return self.heads[(repository, ref)]

        def read_file(self, repository, path, ref):
            raise AssertionError("queue CLI is ref-read only")

        def create_branch(self, repository, branch, sha):
            raise AssertionError("queue CLI is read-only")

        def put_file(
            self,
            repository,
            path,
            branch,
            content,
            message,
            expected_blob_sha=None,
        ):
            raise AssertionError("queue CLI is read-only")

    transport = FakeTransport()
    monkeypatch.setattr(
        portfolio_module,
        "GitHubRestTransport",
        lambda token=None: transport,
    )
    monkeypatch.setattr(
        queue_module,
        "GitHubRestTransport",
        lambda token=None: transport,
    )
    state_db = tmp_path / "queue-cli.sqlite3"

    assert main(["portfolio-cycle", "--state-db", str(state_db)]) == 0
    baseline = json.loads(capsys.readouterr().out)
    assert baseline["baseline"] is True

    transport.heads[
        ("thebrazenbeard/chat-communication-bus", "main")
    ] = "d" * 40
    assert main(["portfolio-cycle", "--state-db", str(state_db)]) == 0
    scheduled = json.loads(capsys.readouterr().out)
    assert scheduled["queued"] == 1

    assert main(
        [
            "consume-queue",
            "--state-db",
            str(state_db),
            "--holder",
            "cli-test",
            "--lease-ttl",
            "60",
        ]
    ) == 0
    consumed = json.loads(capsys.readouterr().out)
    assert consumed["claimed"] is True
    assert consumed["queue_state"] == "COMPLETE"
    assert consumed["operator_status"] == "COMPLETE"
    assert consumed["fencing_token"] == 1

    assert main(["queue-status", "--state-db", str(state_db)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "claims": 1,
        "states": {"COMPLETE": 1},
    }


def test_external_queue_consumption_redacts_private_dependency_failure(
    tmp_path,
    monkeypatch,
):
    registry = tmp_path / "private-queue.yaml"
    registry.write_text(
        """
projects:
- id: private-provider
  name: Private Provider
  visibility: private
  repositories: [secret-owner/private-provider]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-provider-family
  scheduling_state: SCHEDULABLE
- id: private-consumer
  name: Private Consumer
  visibility: private
  repositories: [secret-owner/private-consumer]
  capabilities: [read, analyze]
  assignment_scope: EXTERNAL_BOUNDED
  review_scope: STANDING
  family_id: private-consumer-family
  scheduling_state: SCHEDULABLE
  execution_targets:
  - work_type: INSPECT
    repository: secret-owner/private-consumer
    ref: private-main
""".lstrip(),
        encoding="utf-8",
    )
    _pin_external_registry(registry, monkeypatch)
    private_dependencies = tmp_path / "do-not-leak-private-queue-topology.yaml"

    with pytest.raises(
        ValueError,
        match="external queue consumption is unavailable or structurally invalid",
    ) as exc:
        main(
            [
                "consume-queue",
                "--dependencies",
                str(private_dependencies),
                "--state-db",
                str(tmp_path / "private-state.sqlite3"),
            ]
        )

    message = str(exc.value)
    assert "do-not-leak" not in message
    assert "private-provider" not in message
    assert "private-consumer" not in message
    assert "secret-owner" not in message



def test_worker_route_status_missing_database_is_safe_and_empty(tmp_path, capsys):
    state_db = tmp_path / "missing-worker-routes.sqlite3"

    assert main(
        [
            "worker-route-status",
            "--state-db",
            str(state_db),
        ]
    ) == 0

    assert json.loads(capsys.readouterr().out) == {
        "routes": 0,
        "states": {},
    }


def test_cli_reconcile_queue_releases_safe_read_retry(tmp_path, capsys):
    from runner.models import DependencyEdge, ProjectDefinition
    from runner.portfolio import collect_and_schedule_portfolio
    from runner.queue_consumer import SqliteQueueStore

    class FakeTransport:
        def __init__(self):
            self.heads = {
                ("example/provider", "main"): "a" * 40,
            }

        def read_ref(self, repository, ref):
            return self.heads[(repository, ref)]

        def read_file(self, repository, path, ref):
            raise AssertionError("reconciliation fixture is ref-read only")

        def create_branch(self, repository, branch, sha):
            raise AssertionError("reconciliation fixture is read-only")

        def put_file(
            self,
            repository,
            path,
            branch,
            content,
            message,
            expected_blob_sha=None,
        ):
            raise AssertionError("reconciliation fixture is read-only")

    provider = ProjectDefinition.from_mapping(
        {
            "id": "provider",
            "name": "Provider",
            "visibility": "public",
            "repositories": ["example/provider"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "provider",
            "scheduling_state": "SCHEDULABLE",
        }
    )
    consumer = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "INSPECT",
                    "repository": "example/consumer",
                    "ref": "main",
                }
            ],
        }
    )
    dependency = DependencyEdge.from_mapping(
        {
            "id": "provider-consumer",
            "provider": "provider",
            "consumer": "consumer",
            "kind": "source",
            "selector": {
                "repository": "example/provider",
                "ref": "main",
            },
            "reaction": "INSPECT",
            "evidence": "exact-subject",
        }
    )
    projects = (provider, consumer)
    transport = FakeTransport()
    state_db = tmp_path / "reconcile-cli.sqlite3"

    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(dependency,),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=state_db,
        token=None,
        transport=transport,
        clock=lambda: 1.0,
    )
    transport.heads[("example/provider", "main")] = "b" * 40
    collect_and_schedule_portfolio(
        projects=projects,
        dependencies=(dependency,),
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        state_db=state_db,
        token=None,
        transport=transport,
        clock=lambda: 2.0,
    )

    store = SqliteQueueStore(state_db)
    claim = store.claim_next(
        projects=projects,
        registry_digest="1" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="3" * 64,
        holder="cli-reconcile",
        now=3.0,
        ttl=60.0,
    )
    assert claim is not None
    store.finalize(
        claim,
        state="OUTCOME_UNKNOWN",
        reason="synthetic ambiguity",
        now=4.0,
    )
    store.close()

    assert main(
        [
            "reconcile-queue",
            "--snapshot-id",
            str(claim.snapshot_id),
            "--frontier-fingerprint",
            claim.frontier_fingerprint,
            "--expected-fencing-token",
            str(claim.fencing_token),
            "--resolution",
            "RELEASE_RETRY_READ_ONLY",
            "--evidence-sha256",
            "e" * 64,
            "--reconciler",
            "cli-test",
            "--state-db",
            str(state_db),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "M6_QUEUE_RECONCILIATION"
    assert payload["previous_state"] == "OUTCOME_UNKNOWN"
    assert payload["final_state"] == "FAILED_RETRYABLE"
    assert payload["attempt_generation"] == 2
    assert payload["frontier_fingerprint"] == claim.frontier_fingerprint



def test_cli_worker_route_pull_and_receipt_round_trip(
    tmp_path,
    monkeypatch,
    capsys,
):
    from types import SimpleNamespace

    from runner.models import (
        Frontier,
        Observation,
        ProjectDefinition,
        WorkerDefinition,
    )
    from runner.portfolio import SqlitePortfolioStore
    from runner.worker_routing import (
        SqliteWorkerRouteStore,
        resolve_read_only_worker_route,
    )

    worker = WorkerDefinition.from_mapping(
        {
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "EXECUTABLE",
            "locators": {"model": "reviewer-model"},
            "roles": ["review"],
            "routes": {"OPENAI_AGENT_API": "VERIFIED"},
            "route_contracts": {
                "OPENAI_AGENT_API": {
                    "effect_class": "READ_ONLY",
                    "replay_policy": "SAFE",
                }
            },
            "reconstruction": {
                "repository": "example/workers",
                "path": "reviewer.md",
                "commit": "f" * 40,
            },
        }
    )
    project = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": "example/consumer",
                    "ref": "review",
                    "worker_id": "reviewer",
                    "worker_route": "OPENAI_AGENT_API",
                }
            ],
        }
    )
    frontier = Frontier.from_mapping(
        {
            "id": "frontier-review",
            "project": "consumer",
            "subject": {
                "repository": "example/provider",
                "ref": "main",
                "commit": "b" * 40,
            },
            "work_type": "REREVIEW",
            "reason": "provider changed",
            "dependencies": ["provider-review"],
            "required_capabilities": ["analyze"],
            "collision_keys": ["project:consumer"],
            "cost_class": "SMALL",
            "priority_inputs": {
                "fanout": 1,
                "blocked_downstream": 0,
                "staleness_risk": 1,
                "failure_severity": 0,
                "declared_priority": 0,
                "cost": 1,
                "authority_available": 1,
                "scheduling_eligible": 1,
                "executable_now": 1,
            },
            "status": "READY",
        }
    )
    route = resolve_read_only_worker_route(
        project=project,
        frontier=frontier,
        workers=(worker,),
    )
    assert route is not None

    state_db = tmp_path / "worker-cli.sqlite3"
    portfolio = SqlitePortfolioStore(state_db)
    snapshot_id = portfolio.commit_cycle(
        registry_digest="0" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="1" * 64,
        snapshot_digest="3" * 64,
        observed_at=1.0,
        baseline=True,
        observations=(
            Observation.from_mapping(
                {
                    "target": "provider",
                    "evidence_class": "AUTHORITATIVE",
                    "subject": {
                        "repository": "example/provider",
                        "ref": "main",
                        "commit": "b" * 40,
                    },
                    "observed_value": "b" * 40,
                    "observed_at": "test",
                    "observer": "test",
                }
            ),
        ),
        changed_count=0,
        ranked_frontiers=(),
        expected_previous_snapshot_id=None,
        expected_previous_dependency_snapshot_id=None,
    )
    portfolio.close()

    store = SqliteWorkerRouteStore(state_db)
    envelope = store.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="a" * 64,
        queue_fencing_token=3,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="review",
        target_head="c" * 40,
        frontier_json=json.dumps(
            {
                "id": frontier.id,
                "project": frontier.project,
                "subject": {
                    "repository": frontier.subject.repository,
                    "ref": frontier.subject.ref,
                    "commit": frontier.subject.commit,
                    "path": frontier.subject.path,
                    "digest": frontier.subject.digest,
                },
                "work_type": frontier.work_type,
                "reason": frontier.reason,
                "dependencies": list(frontier.dependencies),
                "required_capabilities": list(
                    frontier.required_capabilities
                ),
                "collision_keys": list(frontier.collision_keys),
                "cost_class": frontier.cost_class.value,
                "priority_inputs": dict(frontier.priority_inputs),
                "status": frontier.status.value,
            },
            sort_keys=True,
        ),
        now=1.0,
    )
    store.close()

    monkeypatch.setattr(
        cli_module,
        "_load_worker_registry_snapshot",
        lambda: SimpleNamespace(
            workers=(worker,),
            sha256="1" * 64,
        ),
    )
    payload_out = tmp_path / "worker-packet.json"
    if os.name == "nt":
        # Private packets must not inherit a broadly readable parent ACL.
        subprocess.run(
            ["icacls", str(tmp_path), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
            check=True, capture_output=True, text=True,
        )
        # Replacement must not preserve an older, overly broad file ACL.
        payload_out.write_text("stale fixture", encoding="utf-8")

    assert main(
        [
            "claim-worker-route",
            "--worker-id",
            "reviewer",
            "--route",
            "OPENAI_AGENT_API",
            "--holder",
            "cli-worker",
            "--lease-ttl",
            "60",
            "--payload-out",
            str(payload_out),
            "--state-db",
            str(state_db),
        ]
    ) == 0
    claimed = json.loads(capsys.readouterr().out)
    assert claimed["claimed"] is True
    assert claimed["route_id"] == envelope.route_id
    assert claimed["delivery_fencing_token"] == 1
    packet = json.loads(payload_out.read_text(encoding="utf-8"))
    if os.name == "nt":
        # Python's Windows st_mode does not represent NTFS discretionary ACLs.
        # Verify that the packet has no broadly readable default grants instead.
        acl = subprocess.run(
            ["icacls", str(payload_out)], check=True, text=True,
            capture_output=True,
        ).stdout.lower()
        assert "(f)" in acl
        for broad_principal in (
            "everyone:", "authenticated users:", "builtin\\users:",
            "s-1-1-0:", "s-1-5-11:", "s-1-5-32-545:",
        ):
            assert broad_principal not in acl
    else:
        assert payload_out.stat().st_mode & 0o777 == 0o600
    assert packet["target_repository"] == "example/consumer"
    assert packet["target_ref"] == "review"
    assert packet["target_head"] == "c" * 40
    assert packet["frontier"]["work_type"] == "REREVIEW"

    assert main(
        [
            "record-worker-receipt",
            "--route-id",
            envelope.route_id,
            "--worker-id",
            "reviewer",
            "--route",
            "OPENAI_AGENT_API",
            "--holder",
            "cli-worker",
            "--expected-fencing-token",
            "1",
            "--receipt-class",
            "SUCCEEDED",
            "--receipt-sha256",
            "d" * 64,
            "--state-db",
            str(state_db),
        ]
    ) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["state"] == "RECEIPT_RECORDED"
    assert receipt["receipt_class"] == "SUCCEEDED"
    assert receipt["receipt_sha256"] == "d" * 64



def test_private_worker_packet_cannot_be_written_inside_public_checkout(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "PROJECT_RUNNER_PROJECT_REGISTRY",
        str(tmp_path / "external-projects.yaml"),
    )
    state_db = tmp_path / "private-worker-state.sqlite3"
    payload_out = cli_module.ROOT / ".project-runner" / "private-packet.json"

    with pytest.raises(
        ValueError,
        match="outside the public checkout",
    ):
        main(
            [
                "claim-worker-route",
                "--worker-id",
                "reviewer",
                "--route",
                "OPENAI_AGENT_API",
                "--holder",
                "private-worker",
                "--payload-out",
                str(payload_out),
                "--state-db",
                str(state_db),
            ]
        )

    assert not state_db.exists()
    assert not payload_out.exists()


def test_worker_route_pull_inside_checkout_skips_private_and_claims_public(
    tmp_path,
    monkeypatch,
    capsys,
):
    from types import SimpleNamespace

    from runner.models import (
        Frontier,
        Observation,
        ProjectDefinition,
        WorkerDefinition,
    )
    from runner.portfolio import SqlitePortfolioStore
    from runner.worker_routing import (
        SqliteWorkerRouteStore,
        resolve_read_only_worker_route,
    )

    worker = WorkerDefinition.from_mapping(
        {
            "id": "reviewer",
            "name": "Reviewer",
            "worker_type": "OPENAI_AGENT",
            "lifecycle": "EXECUTABLE",
            "locators": {"model": "reviewer-model"},
            "roles": ["review"],
            "routes": {"OPENAI_AGENT_API": "VERIFIED"},
            "route_contracts": {
                "OPENAI_AGENT_API": {
                    "effect_class": "READ_ONLY",
                    "replay_policy": "SAFE",
                }
            },
            "reconstruction": {
                "repository": "example/workers",
                "path": "reviewer.md",
                "commit": "f" * 40,
            },
        }
    )
    project = ProjectDefinition.from_mapping(
        {
            "id": "consumer",
            "name": "Consumer",
            "visibility": "public",
            "repositories": ["example/consumer"],
            "capabilities": ["read", "analyze"],
            "assignment_scope": "NONE",
            "review_scope": "NONE",
            "family_id": "consumer",
            "scheduling_state": "SCHEDULABLE",
            "execution_targets": [
                {
                    "work_type": "REREVIEW",
                    "repository": "example/consumer",
                    "ref": "review",
                    "worker_id": "reviewer",
                    "worker_route": "OPENAI_AGENT_API",
                }
            ],
        }
    )
    frontier = Frontier.from_mapping(
        {
            "id": "frontier-review",
            "project": "consumer",
            "subject": {
                "repository": "example/provider",
                "ref": "main",
                "commit": "b" * 40,
            },
            "work_type": "REREVIEW",
            "reason": "provider changed",
            "dependencies": ["provider-review"],
            "required_capabilities": ["analyze"],
            "collision_keys": ["project:consumer"],
            "cost_class": "SMALL",
            "priority_inputs": {
                "fanout": 1,
                "blocked_downstream": 0,
                "staleness_risk": 1,
                "failure_severity": 0,
                "declared_priority": 0,
                "cost": 1,
                "authority_available": 1,
                "scheduling_eligible": 1,
                "executable_now": 1,
            },
            "status": "READY",
        }
    )
    route = resolve_read_only_worker_route(
        project=project,
        frontier=frontier,
        workers=(worker,),
    )
    assert route is not None

    state_db = tmp_path / "worker-cli-private-filter.sqlite3"
    portfolio = SqlitePortfolioStore(state_db)
    snapshot_id = portfolio.commit_cycle(
        registry_digest="0" * 64,
        dependency_digest="2" * 64,
        worker_registry_digest="1" * 64,
        snapshot_digest="3" * 64,
        observed_at=1.0,
        baseline=True,
        observations=(
            Observation.from_mapping(
                {
                    "target": "provider",
                    "evidence_class": "AUTHORITATIVE",
                    "subject": {
                        "repository": "example/provider",
                        "ref": "main",
                        "commit": "b" * 40,
                    },
                    "observed_value": "b" * 40,
                    "observed_at": "test",
                    "observer": "test",
                }
            ),
        ),
        changed_count=0,
        ranked_frontiers=(),
        expected_previous_snapshot_id=None,
        expected_previous_dependency_snapshot_id=None,
    )
    portfolio.close()

    frontier_json = json.dumps(
        {
            "id": frontier.id,
            "project": frontier.project,
            "subject": {
                "repository": frontier.subject.repository,
                "ref": frontier.subject.ref,
                "commit": frontier.subject.commit,
                "path": frontier.subject.path,
                "digest": frontier.subject.digest,
            },
            "work_type": frontier.work_type,
            "reason": frontier.reason,
            "dependencies": list(frontier.dependencies),
            "required_capabilities": list(frontier.required_capabilities),
            "collision_keys": list(frontier.collision_keys),
            "cost_class": frontier.cost_class.value,
            "priority_inputs": dict(frontier.priority_inputs),
            "status": frontier.status.value,
        },
        sort_keys=True,
    )

    store = SqliteWorkerRouteStore(state_db)
    private_envelope = store.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="a" * 64,
        queue_fencing_token=1,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="review",
        target_head="c" * 40,
        frontier_json=frontier_json,
        now=1.0,
        packet_sensitivity="PRIVATE",
    )
    public_envelope = store.enqueue(
        snapshot_id=snapshot_id,
        frontier_fingerprint="b" * 64,
        queue_fencing_token=2,
        worker_registry_digest="1" * 64,
        route=route,
        target_repository="example/consumer",
        target_ref="review",
        target_head="c" * 40,
        frontier_json=frontier_json,
        now=2.0,
        packet_sensitivity="PUBLIC",
    )
    store.close()

    monkeypatch.delenv("PROJECT_RUNNER_PROJECT_REGISTRY", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_worker_registry_snapshot",
        lambda: SimpleNamespace(
            workers=(worker,),
            sha256="1" * 64,
        ),
    )
    checkout = tmp_path / "public-checkout"
    checkout.mkdir()
    monkeypatch.setattr(cli_module, "ROOT", checkout)
    payload_out = checkout / ".project-runner" / "worker-packet.json"

    assert main(
        [
            "claim-worker-route",
            "--worker-id",
            "reviewer",
            "--route",
            "OPENAI_AGENT_API",
            "--holder",
            "cli-worker",
            "--lease-ttl",
            "60",
            "--payload-out",
            str(payload_out),
            "--state-db",
            str(state_db),
        ]
    ) == 0

    claimed = json.loads(capsys.readouterr().out)
    assert claimed["claimed"] is True
    assert claimed["route_id"] == public_envelope.route_id
    assert payload_out.exists()
    packet = json.loads(payload_out.read_text(encoding="utf-8"))
    assert packet["packet_sensitivity"] == "PUBLIC"

    store = SqliteWorkerRouteStore(state_db)
    try:
        private_state = store.connection.execute(
            """
            SELECT state, delivery_fencing_token
            FROM worker_route_outbox
            WHERE route_id = ?
            """,
            (private_envelope.route_id,),
        ).fetchone()
        public_state = store.connection.execute(
            """
            SELECT state, delivery_fencing_token
            FROM worker_route_outbox
            WHERE route_id = ?
            """,
            (public_envelope.route_id,),
        ).fetchone()
    finally:
        store.close()

    assert private_state == ("PENDING", 0)
    assert public_state == ("CLAIMED", 1)
