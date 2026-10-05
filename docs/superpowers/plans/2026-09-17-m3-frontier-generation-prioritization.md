# M3 Frontier Generation and Prioritization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn M2 invalidations and other evidence-backed gaps into deterministic frontier records, deduplicate equivalent work, partition collisions, and rank executable work with explicit reasons while keeping blocked work from stalling unrelated work.

**Architecture:** M3 is a pure orchestration-decision layer on top of M2. `runner/frontier.py` derives normalized frontier records from invalidations and explicit gap inputs; `runner/prioritize.py` computes deterministic explainable ranking; `runner/collisions.py` partitions overlapping collision domains; `runner/dedup.py` removes equivalent frontiers using a semantic fingerprint. No downstream execution or authority expansion is introduced in M3.

**Tech Stack:** Python 3.12, dataclasses/enums, PyYAML, jsonschema, pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-project-runner-design.md`

## Global Constraints

- Observation is not authority.
- Coordination is not authorization.
- Public-safe repository state only.
- Frontier status must distinguish executable work from dependency- or authority-blocked work.
- Priority must be deterministic and explainable; ranking reasons are first-class output.
- A blocked high-priority frontier must not prevent unrelated executable work from advancing.
- Equivalent work deduplicates by stable semantic inputs, not scheduling order.
- Overlapping collision keys must not execute concurrently unless explicitly declared safe.
- M3 does not dispatch workers or mutate downstream repositories.

---

### Task 1: Frontier Model and Schema

**Files:**
- Modify: `runner/models.py`
- Create: `schemas/frontier.schema.json`
- Modify: `runner/schema.py`
- Create: `tests/unit/test_frontier_model.py`

**Interfaces:**
- Consumes: M2 `Invalidation`, `Subject`, `DependencyReaction`.
- Produces: `FrontierStatus`, `CostClass`, `Frontier`, `Frontier.from_mapping()` and schema validation for `frontier` documents.

- [ ] **Step 1: Write failing tests**

```python
from runner.models import Frontier, FrontierStatus


def test_frontier_rejects_ready_without_required_capabilities_field():
    payload = {
        "id": "f-1",
        "project": "vera",
        "subject": {"repository": "thebrazenbeard/vera", "ref": "main", "commit": "abc"},
        "work_type": "REREVIEW",
        "reason": "provider moved",
        "dependencies": [],
        "collision_keys": ["repo:thebrazenbeard/vera"],
        "cost_class": "SMALL",
        "priority_inputs": {},
        "status": "READY",
    }
    try:
        Frontier.from_mapping(payload)
    except (KeyError, ValueError):
        pass
    else:
        raise AssertionError("missing required_capabilities must fail closed")


def test_frontier_status_includes_waiting_authority():
    assert FrontierStatus.WAITING_AUTHORITY.value == "WAITING_AUTHORITY"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/unit/test_frontier_model.py -q`
Expected: FAIL because frontier types do not exist.

- [ ] **Step 3: Implement minimal frontier types and schema**

Add enums and immutable dataclass fields matching spec section 5.5. Require stable id, project, exact subject, work type, reason, dependencies, required capabilities, collision keys, cost class, priority inputs, and status. Add `frontier` to `_SCHEMA_FILES`.

- [ ] **Step 4: Run tests and full schema/model tests**

Run: `python -m pytest tests/unit/test_frontier_model.py tests/unit/test_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add frontier model and schema`

---

### Task 2: Frontier Generation from Invalidations and Gaps

**Files:**
- Create: `runner/frontier.py`
- Create: `tests/unit/test_frontier_generation.py`
- Create: `tests/fixtures/frontier-gaps.yaml`

**Interfaces:**
- Consumes: `tuple[Invalidation, ...]`, project capability declarations, optional explicit public-safe gap records.
- Produces: `derive_frontiers(...) -> tuple[Frontier, ...]`.

- [ ] **Step 1: Write failing tests**

```python
from runner.frontier import derive_frontiers


def test_invalidation_becomes_frontier_with_matching_work_type(sample_invalidation):
    frontiers = derive_frontiers((sample_invalidation,), capability_lookup={"vera": {"read", "analyze"}})
    assert len(frontiers) == 1
    assert frontiers[0].work_type == "REREVIEW"


def test_missing_capability_yields_waiting_authority(sample_invalidation):
    frontiers = derive_frontiers((sample_invalidation,), capability_lookup={"vera": {"read"}})
    assert frontiers[0].status.value == "WAITING_AUTHORITY"
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_frontier_generation.py -q`
Expected: FAIL because generator is absent.

- [ ] **Step 3: Implement derivation**

Map reactions to work types. Infer minimum capabilities conservatively: `INSPECT/RETEST/REREVIEW/REQUALIFY` require `analyze`; `BLOCK` produces `WAITING_DEPENDENCY`; missing required capability produces `WAITING_AUTHORITY`; otherwise status is `READY`. Generate collision keys from exact target repository and optional artifact path. Keep reason text deterministic and public-safe.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_frontier_generation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: derive work frontiers`

---

### Task 3: Stable Frontier Fingerprints and Deduplication

**Files:**
- Create: `runner/dedup.py`
- Create: `tests/unit/test_dedup.py`

**Interfaces:**
- Consumes: frontier semantic fields.
- Produces: `frontier_fingerprint(frontier) -> str` and `deduplicate_frontiers(frontiers) -> tuple[Frontier, ...]`.

- [ ] **Step 1: Write failing tests**

```python
from runner.dedup import deduplicate_frontiers, frontier_fingerprint


def test_fingerprint_ignores_incidental_input_order(frontier_factory):
    a = frontier_factory(dependencies=("b", "a"), collision_keys=("y", "x"))
    b = frontier_factory(dependencies=("a", "b"), collision_keys=("x", "y"))
    assert frontier_fingerprint(a) == frontier_fingerprint(b)


def test_equivalent_frontiers_deduplicate(frontier_factory):
    a = frontier_factory()
    b = frontier_factory(id="different-id")
    assert len(deduplicate_frontiers((a, b))) == 1
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_dedup.py -q`
Expected: FAIL because dedup module is absent.

- [ ] **Step 3: Implement semantic fingerprint**

Canonicalize operation/work type, exact subject, normalized dependencies, required capabilities, collision keys, and verification-relevant priority/policy fields. Exclude frontier id, creation order, timestamps, and scheduler position. Hash canonical JSON with SHA-256.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_dedup.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: deduplicate equivalent frontiers`

---

### Task 4: Collision Partitioning

**Files:**
- Create: `runner/collisions.py`
- Create: `tests/unit/test_collisions.py`

**Interfaces:**
- Consumes: deduplicated frontiers.
- Produces: `partition_collision_groups(frontiers) -> tuple[tuple[Frontier, ...], ...]` and `independent_frontiers(frontiers) -> tuple[Frontier, ...]`.

- [ ] **Step 1: Write failing tests**

```python
from runner.collisions import partition_collision_groups


def test_shared_collision_key_groups_frontiers(frontier_factory):
    a = frontier_factory(id="a", collision_keys=("repo:x",))
    b = frontier_factory(id="b", collision_keys=("repo:x", "path:y"))
    groups = partition_collision_groups((a, b))
    assert len(groups) == 1
    assert {f.id for f in groups[0]} == {"a", "b"}


def test_unrelated_frontiers_remain_separate(frontier_factory):
    a = frontier_factory(id="a", collision_keys=("repo:x",))
    b = frontier_factory(id="b", collision_keys=("repo:y",))
    groups = partition_collision_groups((a, b))
    assert len(groups) == 2
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_collisions.py -q`
Expected: FAIL because collision module is absent.

- [ ] **Step 3: Implement transitive collision grouping**

Treat collision-key overlap as an undirected graph; connected components form mutually serialized groups. Do not serialize frontiers with no overlapping key. `independent_frontiers` returns at most one currently executable representative per collision component plus all singleton components.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_collisions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: partition frontier collision domains`

---

### Task 5: Deterministic Explainable Prioritization

**Files:**
- Create: `runner/prioritize.py`
- Create: `policy/scheduling.yaml`
- Create: `tests/unit/test_prioritize.py`

**Interfaces:**
- Consumes: frontiers and scheduling weights.
- Produces: `PriorityDecision(frontier, score, reasons)` and `rank_frontiers(frontiers, policy) -> tuple[PriorityDecision, ...]`.

- [ ] **Step 1: Write failing tests**

```python
from runner.prioritize import rank_frontiers


def test_ready_work_ranks_ahead_of_authority_blocked_peer(frontier_factory):
    ready = frontier_factory(id="ready", status="READY", priority_inputs={"declared_priority": 5})
    blocked = frontier_factory(id="blocked", status="WAITING_AUTHORITY", priority_inputs={"declared_priority": 100})
    ranked = rank_frontiers((blocked, ready))
    assert ranked[0].frontier.id == "ready"


def test_priority_exposes_reasons(frontier_factory):
    ranked = rank_frontiers((frontier_factory(priority_inputs={"fanout": 3}),))
    assert any("fanout" in reason for reason in ranked[0].reasons)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/unit/test_prioritize.py -q`
Expected: FAIL because prioritizer is absent.

- [ ] **Step 3: Implement deterministic ranking**

Use explicit integer weights from `policy/scheduling.yaml` for fanout, blocked downstream count, staleness risk, failure severity, declared priority, cost penalty, and executability. Separate executable and blocked classes before numeric scoring so a blocked item cannot suppress unrelated runnable work. Tie-break by stable frontier fingerprint. Emit human-readable reasons for every nonzero factor and for blocked/executable class.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_prioritize.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: rank frontiers with explanations`

---

### Task 6: M3 CLI Sweep and Integration Contract

**Files:**
- Modify: `runner/cli.py`
- Modify: `README.md`
- Modify: `PROJECT_RUNNER.md`
- Create: `tests/integration/test_m3_sweep.py`
- Create: `tests/fixtures/frontier-invalidations.yaml`

**Interfaces:**
- Consumes: M2 invalidations plus registry capabilities and scheduling policy.
- Produces: `project-runner frontier-report ...` deterministic JSON containing deduplicated frontiers, collision groups, ranking, statuses, and reasons.

- [ ] **Step 1: Write failing integration test**

```python
def test_frontier_report_keeps_blocked_and_ready_work_visible(run_cli):
    result = run_cli("frontier-report", "tests/fixtures/frontier-invalidations.yaml")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert {item["status"] for item in payload["frontiers"]} >= {"READY", "WAITING_AUTHORITY"}
    assert payload["ranked"][0]["status"] == "READY"
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/integration/test_m3_sweep.py -q`
Expected: FAIL because command is absent.

- [ ] **Step 3: Implement CLI and documentation**

Wire derivation -> dedup -> collision grouping -> ranking. Output deterministic sorted JSON. Update docs to state M3 can derive and rank work but still cannot dispatch or mutate downstream targets.

- [ ] **Step 4: Run complete repository suite**

Run: `python -m pytest -q`
Expected: all tests PASS.

Run: `project-runner validate`
Expected: registry/schema validation succeeds.

- [ ] **Step 5: Verify exact branch in GitHub Actions**

Push/commit exact M3 head and require the repository `test` workflow to succeed on that exact SHA before proposing integration.

- [ ] **Step 6: Commit**

Commit message: `feat: expose M3 frontier report`
