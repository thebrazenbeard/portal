# M2 Observation and Dependency Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Project Runner able to represent exact observations, load typed dependency edges, determine whether a provider change intersects a declared dependency, and derive the required consumer reaction without broad rerun-everything behavior.

**Architecture:** M2 extends the M1 typed registry kernel with immutable observation and dependency-edge models. Pure graph/currentness functions operate only on exact normalized inputs; provider adapters remain outside the core so GitHub/API collection can be added later without coupling orchestration semantics to one provider.

**Tech Stack:** Python 3.11+, dataclasses, Enum, PyYAML, jsonschema, pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-project-runner-design.md`

## Global Constraints

- Observation does not grant authority.
- Exact-subject evidence outranks convenience pointers.
- Provider movement invalidates only consumers whose declared selectors intersect the changed subject.
- Dependency reactions are one of `NO_ACTION`, `INSPECT`, `RETEST`, `REREVIEW`, `REQUALIFY`, or `BLOCK`.
- Derived currentness must be reproducible from registry + observations; do not maintain narrative status as authority.
- M2 performs no downstream mutation.
- Public repository outputs must remain public-safe.
- Implementation follows test-driven development.

---

### Task 1: Exact observation model and schema

**Files:**
- Modify: `runner/models.py`
- Create: `schemas/observation.schema.json`
- Create: `tests/unit/test_observation.py`

**Interfaces:**
- Produces: `EvidenceClass`, `ExactSubject`, `Observation`, and `Observation.from_mapping()`.
- `ExactSubject` contains repository, ref, optional commit, optional path, optional digest, and exposes `identity()` for deterministic comparison.

- [ ] Write failing tests proving exact subjects with different commits are distinct and malformed observations fail closed.
- [ ] Run `python -m pytest tests/unit/test_observation.py -q` and confirm RED because the model does not exist.
- [ ] Implement the smallest immutable models and schema necessary to satisfy those tests.
- [ ] Run the focused test and then the full suite; require GREEN.
- [ ] Commit as `feat: add exact observation model`.

### Task 2: Dependency edge model, schema, and registry loader

**Files:**
- Modify: `runner/models.py`
- Modify: `runner/registry.py`
- Create: `schemas/dependency.schema.json`
- Create: `topology/dependencies.yaml`
- Create: `tests/unit/test_dependencies.py`

**Interfaces:**
- Produces: `DependencyReaction`, `DependencySelector`, `DependencyEdge`, `load_dependencies(path)`.
- `DependencySelector` supports exact repository plus optional ref/path-prefix matching.

- [ ] Write failing tests for typed edge loading, duplicate dependency IDs, and invalid reaction values.
- [ ] Run the focused tests and confirm RED.
- [ ] Implement the minimal model/schema/loader.
- [ ] Seed a deliberately small topology using only public-safe project identifiers and selectors.
- [ ] Run focused and full suites; require GREEN.
- [ ] Commit as `feat: add dependency registry`.

### Task 3: Selector intersection and dependency graph

**Files:**
- Create: `runner/graph.py`
- Create: `tests/unit/test_graph.py`

**Interfaces:**
- Produces: `selector_matches(selector: DependencySelector, subject: ExactSubject) -> bool`.
- Produces: `affected_edges(edges, changed_subject) -> tuple[DependencyEdge, ...]`.

- [ ] Write failing tests proving exact-repo mismatch, ref mismatch, path-prefix match, and unrelated consumers remain untouched.
- [ ] Run focused tests and confirm RED.
- [ ] Implement deterministic selector matching and affected-edge filtering.
- [ ] Run focused and full suites; require GREEN.
- [ ] Commit as `feat: add dependency intersection graph`.

### Task 4: Currentness and invalidation derivation

**Files:**
- Create: `runner/currentness.py`
- Create: `runner/propagate.py`
- Create: `tests/unit/test_currentness.py`
- Create: `tests/integration/test_invalidation.py`

**Interfaces:**
- Produces: `subject_changed(previous: Observation | None, current: Observation) -> bool`.
- Produces: `Invalidation` with dependency id, provider, consumer, changed subject, and required reaction.
- Produces: `derive_invalidations(previous_observations, current_observations, edges) -> tuple[Invalidation, ...]`.

- [ ] Write failing tests proving unchanged exact subject creates no invalidation, changed commit does, and only intersecting dependency edges propagate.
- [ ] Run focused tests and confirm RED.
- [ ] Implement exact-subject currentness and propagation without provider-specific assumptions.
- [ ] Run focused and full suites; require GREEN.
- [ ] Commit as `feat: derive dependency invalidations`.

### Task 5: Deterministic fixture sweep and CLI exposure

**Files:**
- Modify: `runner/cli.py`
- Create: `tests/fixtures/observations-before.yaml`
- Create: `tests/fixtures/observations-after.yaml`
- Create: `tests/integration/test_m2_sweep.py`
- Modify: `README.md`
- Modify: `PROJECT_RUNNER.md`

**Interfaces:**
- Produces CLI command `project-runner evaluate-change --before <path> --after <path> --dependencies <path>`.
- Output is deterministic JSON containing changed exact subjects and derived invalidations; it does not execute downstream work.

- [ ] Write failing integration test against deterministic before/after fixtures.
- [ ] Run focused integration test and confirm RED.
- [ ] Implement minimal CLI wiring over the pure M2 functions.
- [ ] Document that M2 observes/derives only and grants no cross-repository authority.
- [ ] Run `python -m pytest -q`, `project-runner validate`, and the fixture `evaluate-change` command; require GREEN/exit 0.
- [ ] Commit as `feat: expose M2 change evaluation`.

## M2 Completion Gate

Before integration:

- [ ] Full test suite passes on the exact branch head.
- [ ] GitHub Actions succeeds on that exact head.
- [ ] A provider commit change invalidates only the matching declared consumers in the fixture sweep.
- [ ] An unchanged exact subject generates no work.
- [ ] No M2 code mutates downstream repositories or interprets an observation as authority.
- [ ] `main` has not moved unexpectedly before integration.