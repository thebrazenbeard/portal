# P.O.R.T.A.L. Portfolio Coordinator Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic P.O.R.T.A.L. coordination layer that assigns Project Runner-approved portfolio work to bounded execution nodes without manufacturing authority.

**Architecture:** Preserve `runner/` as the inherited Project Runner execution kernel. Add a separate `portal/` package that composes `plan_wave_admission`, applies node eligibility/capacity routing, and exposes a read-only planning CLI before any live multi-machine dispatch is attempted.

**Tech Stack:** Python 3.12+, dataclasses, PyYAML, pytest, existing Project Runner portfolio interfaces.

## Global Constraints

- P.O.R.T.A.L. means Portfolio Orchestration & Repository Tracking Access Layer.
- Inherited Project Runner exact-subject, collision, authority, currentness, lease, fencing, and verification semantics remain authoritative.
- Portal node placement is scheduling metadata, not authority.
- No protected effect is introduced by this slice.
- No live dispatch is introduced by this slice.
- Existing Project Runner tests must remain green.
- Behavioral changes use recorded red-green TDD.

---

### Task 1: Portal node model and deterministic coordinator

**Files:**
- Create: `portal/__init__.py`
- Create: `portal/models.py`
- Create: `portal/coordinator.py`
- Test: `tests/unit/test_portal_coordinator.py`

**Interfaces:**
- Consumes: `runner.portfolio_advancement.AdvancementWave`, `runner.portfolio_wave_scheduler.WaveExecutionBudget`, `plan_wave_admission(...)`
- Produces: `ExecutionNode`, `PortalAssignment`, `PortalNodeDeferral`, `PortalPlan`, `plan_portal_wave(...)`

- [ ] **Step 1: Add the focused failing test**

Create a minimal two-node fixture and assert that four independent Project Runner admissions are distributed deterministically by lowest load then lexical node ID. Add focused cases for lane allowlists, disabled nodes, capacity exhaustion, duplicate node IDs, and preservation of Project Runner collision deferrals.

- [ ] **Step 2: Verify the relevant failure**

Run: `python -m pytest -q tests/unit/test_portal_coordinator.py`

Expected: non-zero exit because the `portal` package/coordinator does not yet exist.

- [ ] **Step 3: Implement the minimum behavior**

`ExecutionNode` validates a non-empty `node_id`, positive integer `max_parallel`, deterministic unique `allowed_lanes`, and `enabled`.

`plan_portal_wave`:
1. rejects duplicate node IDs;
2. obtains the Project Runner `WaveAdmissionPlan`;
3. walks `selected` in existing order;
4. filters eligible nodes by enabled state, capacity, and lane;
5. chooses `(current_load, node_id)` minimum;
6. emits `NO_EXECUTION_NODE` when none qualify;
7. never changes Project Runner deferrals or authority/effect fields.

- [ ] **Step 4: Verify the focused pass**

Run: `python -m pytest -q tests/unit/test_portal_coordinator.py`

Expected: all focused coordinator tests pass.

- [ ] **Step 5: Run the affected integration check**

Run: `python -m pytest -q tests/unit/test_portfolio_wave_scheduler.py tests/unit/test_portal_coordinator.py`

Expected: existing wave scheduler plus Portal coordinator tests pass.

- [ ] **Step 6: Commit the passing deliverable**

`git add portal tests/unit/test_portal_coordinator.py && git commit -m "feat: add deterministic Portal node coordinator"`

### Task 2: Node manifest loader and planning CLI

**Files:**
- Create: `portal/node_registry.py`
- Create: `portal/cli.py`
- Create: `tests/fixtures/portal-nodes-valid.yaml`
- Create: `tests/integration/test_portal_cli.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: advancement-wave JSON accepted by `load_advancement_wave`; YAML node manifest; `plan_portal_wave(...)`
- Produces: `portal plan --wave <path> --nodes <path> [budget flags]` deterministic JSON output

- [ ] **Step 1: Add the focused failing test**

Assert valid manifest loading, duplicate-ID rejection, invalid capacity rejection, and deterministic CLI JSON containing Project Runner selection/deferral counts plus Portal assignment/node-deferral counts.

- [ ] **Step 2: Verify the relevant failure**

Run: `python -m pytest -q tests/integration/test_portal_cli.py`

Expected: non-zero exit because the loader/CLI and `portal` console script do not yet exist.

- [ ] **Step 3: Implement the minimum behavior**

Define schema `PORTAL_EXECUTION_NODES_V1` with a top-level `nodes` list. Reject unknown top-level shape, duplicate IDs, invalid capacities, non-list allowed lanes, and empty lane strings. Add `portal = "portal.cli:entrypoint"` while retaining `project-runner = "runner.cli:entrypoint"`.

The CLI emits JSON only and performs no dispatch.

- [ ] **Step 4: Verify the focused pass**

Run: `python -m pytest -q tests/integration/test_portal_cli.py`

Expected: all Portal CLI tests pass.

- [ ] **Step 5: Run the affected integration check**

Run: `python -m pytest -q tests/unit/test_portal_coordinator.py tests/integration/test_portal_cli.py tests/integration/test_portfolio_wave_cli.py`

Expected: all pass.

- [ ] **Step 6: Commit the passing deliverable**

`git add portal pyproject.toml tests && git commit -m "feat: add Portal planning CLI"`

### Task 3: Portal identity, provenance, and operator documentation

**Files:**
- Create: `PORTAL.md`
- Modify: `README.md`
- Modify: `.portal-bootstrap.json`
- Test: `tests/integration/test_portal_repository_contract.py`

**Interfaces:**
- Consumes: exact bootstrap provenance `project-runner@04702abbf51aa2920b7d054275619253ea6fa748`
- Produces: repository-visible identity and invariants for future Portal work

- [ ] **Step 1: Add the focused failing test**

Assert the authoritative acronym expansion, provenance commit, presence of both `portal` and `project-runner` console scripts, and explicit statement that Portal planning does not grant execution authority.

- [ ] **Step 2: Verify the relevant failure**

Run: `python -m pytest -q tests/integration/test_portal_repository_contract.py`

Expected: non-zero exit because Portal identity documentation has not yet replaced the inherited Project Runner landing page.

- [ ] **Step 3: Implement the minimum behavior**

Rewrite the landing section around P.O.R.T.A.L., clearly label `runner/` as inherited execution substrate, document `portal plan`, and preserve the exact bootstrap source commit/tree.

- [ ] **Step 4: Verify the focused pass**

Run: `python -m pytest -q tests/integration/test_portal_repository_contract.py`

Expected: pass.

- [ ] **Step 5: Run the affected integration check**

Run: `python -m pytest -q tests/integration/test_repository_contract.py tests/integration/test_portal_repository_contract.py`

Expected: both repository-contract suites pass, with inherited Project Runner contract adjusted only where Portal identity requires it.

- [ ] **Step 6: Commit the passing deliverable**

`git add README.md PORTAL.md .portal-bootstrap.json tests/integration/test_portal_repository_contract.py && git commit -m "docs: establish P.O.R.T.A.L. repository identity"`

### Task 4: Full qualification and frozen handoff

**Files:**
- Modify only defects revealed by qualification in files owned by Tasks 1-3.

**Interfaces:**
- Consumes: complete branch at exact head
- Produces: test/CI evidence and a frozen exact-head review subject

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`

Expected: all inherited and Portal tests pass with no hidden test failures.

- [ ] **Step 2: Validate both CLIs**

Run: `project-runner validate`
Expected: inherited registry validation passes.

Run: `portal --help`
Expected: Portal CLI is installed and exposes `plan`.

- [ ] **Step 3: Verify bootstrap provenance**

Confirm `.portal-bootstrap.json` still binds source repository, commit `04702abbf51aa2920b7d054275619253ea6fa748`, and source tree `f3f228258bc2de73724c598721bd5aa5beedab95`.

- [ ] **Step 4: Freeze exact head**

Push the passing branch and record its exact commit SHA. Do not merge automatically; subsequent live node discovery/dispatch is a new review subject.
