# M1 Kernel + Worker Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first testable Project Runner kernel: typed project/worker models, schema-backed registry loading, the five-project seed registry, the twelve supplied Custom GPT worker registrations, a validation CLI, and CI.

**Architecture:** Keep M1 deliberately read-only and public-safe. YAML files are declarative input; JSON Schema catches structural defects; typed Python models enforce semantic invariants that schemas alone cannot express, especially lifecycle/route claims and duplicate IDs. No downstream mutation or dispatch exists in M1.

**Tech Stack:** Python 3.12, `dataclasses`, `enum`, PyYAML 6.x, jsonschema 4.x, pytest 8.x, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-project-runner-design.md` plus `docs/superpowers/specs/2026-09-17-worker-registry-amendment.md`

## Global Constraints

- Public-safe by construction; no secrets, private payloads, private autobiographical material, or confidential project contents.
- Observation is not authority; registry declarations do not grant downstream permissions.
- Worker locators alone yield only `REGISTERED` status.
- Invocation routes are independently verified; one route never implies another.
- Child/worker authority may never exceed the authority attached to the work unit and target policy.
- M1 performs validation and inventory only; it does not dispatch, merge, deploy, or mutate downstream repositories.
- Use test-driven development for production behavior.

---

### Task 1: Package scaffold and typed core models

**Files:**
- Create: `pyproject.toml`
- Create: `runner/__init__.py`
- Create: `runner/models.py`
- Create: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `WorkerLifecycle`, `RouteState`, `WorkerType`, `InvocationRoute`, `WorkerDefinition`, `ProjectDefinition`.
- Later tasks rely on `WorkerDefinition.from_mapping(data: dict) -> WorkerDefinition` and `ProjectDefinition.from_mapping(data: dict) -> ProjectDefinition`.

- [ ] **Step 1: Write failing model tests**

```python
from runner.models import WorkerDefinition, WorkerLifecycle


def test_worker_locator_defaults_to_registered_only():
    worker = WorkerDefinition.from_mapping({
        "id": "tkal-in-ket-quorum",
        "name": "T'Kal-in-ket Quorum",
        "worker_type": "CHATGPT_CUSTOM_GPT",
        "lifecycle": "REGISTERED",
        "locators": {"gpt_id": "g-68c3e43aabc4819180ef0a89a10c4eb6"},
        "routes": {},
        "roles": [],
    })
    assert worker.lifecycle is WorkerLifecycle.REGISTERED


def test_invalid_gpt_id_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="GPT id"):
        WorkerDefinition.from_mapping({
            "id": "bad",
            "name": "Bad",
            "worker_type": "CHATGPT_CUSTOM_GPT",
            "lifecycle": "REGISTERED",
            "locators": {"gpt_id": "not-a-gpt-id"},
            "routes": {},
            "roles": [],
        })


def test_executable_worker_requires_verified_route():
    import pytest
    with pytest.raises(ValueError, match="EXECUTABLE"):
        WorkerDefinition.from_mapping({
            "id": "tkal",
            "name": "T'Kal",
            "worker_type": "CHATGPT_CUSTOM_GPT",
            "lifecycle": "EXECUTABLE",
            "locators": {"gpt_id": "g-68c3e43aabc4819180ef0a89a10c4eb6"},
            "routes": {"RUNNER_ACTION_PULL": "UNVERIFIED"},
            "roles": ["hostile_review"],
        })
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/unit/test_models.py -q`
Expected: collection/import failure because `runner.models` does not yet exist.

- [ ] **Step 3: Implement minimal typed models**

Implement enums for the lifecycle, route state, worker type, and route names. `WorkerDefinition.from_mapping` validates `g-` IDs with `^g-[A-Za-z0-9]+$`; `EXECUTABLE` requires at least one route state `VERIFIED`; `CONNECTED` requires at least one route state `VERIFIED` or `CONNECTED`. Keep project typing minimal: stable id, name, repositories, visibility, capabilities.

- [ ] **Step 4: Run model tests and full suite**

Run: `python -m pytest tests/unit/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add typed runner registry models`

---

### Task 2: JSON Schemas and schema loader

**Files:**
- Create: `schemas/project.schema.json`
- Create: `schemas/worker.schema.json`
- Create: `runner/schema.py`
- Create: `tests/unit/test_schema.py`

**Interfaces:**
- Produces: `validate_document(schema_name: str, payload: object) -> None`.
- Consumes schema files from `schemas/` relative to repository root.

- [ ] **Step 1: Write failing schema tests**

```python
import pytest
from jsonschema import ValidationError
from runner.schema import validate_document


def test_worker_schema_rejects_missing_id():
    with pytest.raises(ValidationError):
        validate_document("worker", {"workers": [{"name": "missing id"}]})


def test_project_schema_accepts_minimal_project():
    validate_document("project", {
        "projects": [{
            "id": "project-runner",
            "name": "Project Runner",
            "visibility": "public",
            "repositories": ["thebrazenbeard/project-runner"],
            "capabilities": ["read", "analyze", "propose", "write_branch"]
        }]
    })
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_schema.py -q`
Expected: FAIL because `runner.schema` and schemas do not exist.

- [ ] **Step 3: Implement schemas and validator**

`worker.schema.json` requires a top-level `workers` array and per-worker `id`, `name`, `worker_type`, `lifecycle`, `locators`, `roles`, and `routes`. `project.schema.json` requires top-level `projects` and the project fields shown above. Set `additionalProperties: false` for registry records so typos fail closed.

`runner/schema.py` loads the named schema and invokes `jsonschema.Draft202012Validator(...).validate(payload)`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add registry JSON schemas`

---

### Task 3: Registry loader with duplicate and semantic validation

**Files:**
- Create: `runner/registry.py`
- Create: `tests/unit/test_registry.py`
- Create: `tests/fixtures/workers-valid.yaml`
- Create: `tests/fixtures/projects-valid.yaml`

**Interfaces:**
- Produces:
  - `load_workers(path: Path) -> tuple[WorkerDefinition, ...]`
  - `load_projects(path: Path) -> tuple[ProjectDefinition, ...]`
- Uses `validate_document` before constructing typed models.

- [ ] **Step 1: Write failing registry tests**

```python
from pathlib import Path
import pytest
from runner.registry import load_workers


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
    with pytest.raises(ValueError, match="duplicate worker id"):
        load_workers(path)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_registry.py -q`
Expected: FAIL because loader is missing.

- [ ] **Step 3: Implement loader**

Use `yaml.safe_load`, schema validation, tuple construction, and duplicate-id rejection. Error messages must include record type and duplicate id.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add validated registry loaders`

---

### Task 4: Populate project and Custom GPT worker registries

**Files:**
- Create: `registry/projects.yaml`
- Create: `registry/workers.yaml`
- Create: `registry/capabilities.yaml`
- Create: `tests/integration/test_seed_registries.py`

**Interfaces:**
- Consumes `load_projects` and `load_workers`.
- Produces valid seed data for future graph/dispatch milestones.

- [ ] **Step 1: Write failing seed-registry test**

```python
from pathlib import Path
from runner.registry import load_projects, load_workers

ROOT = Path(__file__).resolve().parents[2]


def test_seed_registry_contains_expected_projects_and_workers():
    projects = load_projects(ROOT / "registry/projects.yaml")
    workers = load_workers(ROOT / "registry/workers.yaml")
    assert {p.id for p in projects} == {
        "project-runner", "chat-communication-bus", "vera",
        "vera-control-plane", "hc-brain"
    }
    assert len(workers) == 12
    assert all(w.lifecycle.value == "REGISTERED" for w in workers)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/integration/test_seed_registries.py -q`
Expected: FAIL because registry files do not exist.

- [ ] **Step 3: Populate seed projects**

Create the five public-safe seed entries. `project-runner` may declare repo-scoped capabilities already granted in chat; downstream projects default conservatively to `read`, `analyze`, and `propose` unless fresher target-specific authority is encoded later.

- [ ] **Step 4: Populate all twelve supplied GPT workers**

Each Custom GPT record starts at `REGISTERED`, with its stable GPT ID/share URL and every connectivity route set to `UNVERIFIED`. Do not infer specialties from names except a human-readable `name`; leave `roles: []` unless the role is supported by project evidence already encoded in the amendment.

- [ ] **Step 5: Run integration and full tests**

Run: `python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

Commit message: `data: seed projects and Custom GPT workers`

---

### Task 5: Validation CLI and readable inventory output

**Files:**
- Create: `runner/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/integration/test_cli.py`

**Interfaces:**
- Produces command: `project-runner validate`
- Produces command: `project-runner inventory`
- `validate` exits 0 only when project and worker registries validate.
- `inventory` emits counts and lifecycle totals, not private payloads.

- [ ] **Step 1: Write failing CLI tests**

```python
from runner.cli import main


def test_validate_command_returns_zero(capsys):
    assert main(["validate"]) == 0
    assert "registries valid" in capsys.readouterr().out.lower()


def test_inventory_reports_twelve_registered_workers(capsys):
    assert main(["inventory"]) == 0
    out = capsys.readouterr().out
    assert "workers: 12" in out.lower()
    assert "registered: 12" in out.lower()
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/integration/test_cli.py -q`
Expected: FAIL because `runner.cli` does not exist.

- [ ] **Step 3: Implement CLI**

Use `argparse`; resolve repository root from `Path(__file__).resolve().parents[1]`; call registry loaders. Add `[project.scripts] project-runner = "runner.cli:entrypoint"` to `pyproject.toml` where `entrypoint()` raises `SystemExit(main())`.

- [ ] **Step 4: Run tests and commands**

Run: `python -m pytest -q`
Run: `python -m runner.cli validate`
Run: `python -m runner.cli inventory`
Expected: all tests PASS; validator prints success; inventory reports five projects and twelve registered workers.

- [ ] **Step 5: Commit**

Commit message: `feat: add registry validation CLI`

---

### Task 6: CI plus public-facing repository docs

**Files:**
- Create: `.github/workflows/test.yml`
- Create: `README.md`
- Create: `PROJECT_RUNNER.md`
- Create: `tests/integration/test_repository_contract.py`

**Interfaces:**
- CI installs the package and runs `pytest` plus `project-runner validate` on pushes and pull requests.
- Documentation states M1's actual implemented capability ceiling and links to the design/specs.

- [ ] **Step 1: Write failing repository-contract test**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_required_m1_repository_files_exist():
    required = [
        "README.md",
        "PROJECT_RUNNER.md",
        ".github/workflows/test.yml",
        "registry/projects.yaml",
        "registry/workers.yaml",
    ]
    assert [p for p in required if not (ROOT / p).exists()] == []
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/integration/test_repository_contract.py -q`
Expected: FAIL listing the not-yet-created docs/workflow.

- [ ] **Step 3: Add CI workflow**

Use `actions/checkout` and `actions/setup-python` pinned to immutable commit SHAs selected at implementation time. Set top-level `permissions: contents: read`. Run Python 3.12, `pip install -e .`, `pytest -q`, and `project-runner validate`.

- [ ] **Step 4: Add README and PROJECT_RUNNER docs**

README explains what Project Runner is, current M1 capability, quickstart, and safety boundary. `PROJECT_RUNNER.md` defines operational invariants: public-safe data, derived state vs authority, no downstream mutation in M1, and lifecycle semantics.

- [ ] **Step 5: Run full verification**

Run: `python -m pytest -q`
Run: `python -m runner.cli validate`
Run: `python -m runner.cli inventory`
Expected: all green; five projects; twelve workers; twelve `REGISTERED`.

- [ ] **Step 6: Commit**

Commit message: `ci: validate Project Runner M1 kernel`

---

## Plan self-review

- Spec coverage for M1: scaffold, typed models, registries, schemas, validation, seed portfolio, worker registry, CI are covered.
- Explicitly deferred to later plans: dependency graph/currentness (M2), frontier engine (M3), recursive dispatcher (M4), GitHub Actions orchestration backend beyond CI (M5), live portfolio integration (M6).
- No downstream write/merge/deploy path is introduced by this plan.
- No worker is promoted beyond evidence-supported `REGISTERED` state.
