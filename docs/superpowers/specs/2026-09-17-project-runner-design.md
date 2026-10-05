# Project Runner — Architecture Design

Date: 2026-09-17
Status: DESIGN APPROVED IN CHAT; IMPLEMENTATION PENDING SPEC REVIEW
Repository: `thebrazenbeard/project-runner`

## 1. Purpose

Project Runner is the public orchestration kernel for Patrick's multi-repository project ecosystem. Its job is to discover work, model dependencies, determine what became stale when upstream state changes, dispatch independent work safely, collect evidence, and continue advancing executable frontiers without turning itself into the source of truth for every downstream project.

Project Runner is intentionally an execution fabric, not a replacement for project repositories, the Chat Communication Bus, Vera's control plane, or project-specific governance.

Core questions it must answer continuously:

1. What exists?
2. What changed?
3. What depends on what changed?
4. What work is now justified and authorized?
5. What evidence demonstrates that the work actually completed?

## 2. Architectural Principles

### 2.1 Observation is not authority

Project Runner may observe, derive, rank, and route work. Its observation of a project does not grant authority over that project.

### 2.2 Coordination is not authorization

A runner decision that a task is ready does not itself authorize a protected effect in the target repository. Cross-repository write, merge, deploy, credential, provider, publication, destructive, or other protected effects remain governed by the target project's authority model.

### 2.3 Public-safe by construction

The repository is public. Therefore Project Runner source, schemas, workflows, derived public metadata, and durable receipts stored here must be safe for public disclosure. It must not contain credentials, private source payloads, private relational/autobiographical data, proprietary confidential mechanisms, or private project content.

Private projects may be represented by sanitized identifiers, hashes, state classes, dependency selectors, and public-safe status metadata. If durable private orchestration state becomes necessary later, it should live on a separate private surface rather than weakening this invariant.

### 2.4 Exact-subject currentness

Project Runner must reason about exact repository subjects: repository, ref, commit/head, artifact path, digest, review subject, workflow run, or other exact locator. Human-readable convenience pointers are never treated as authoritative when stronger exact evidence is available.

### 2.5 Movement propagates consequences

A provider change should invalidate only the consumers whose declared dependency selectors intersect that change. The runner should avoid broad "rerun everything" behavior when the affected surface can be determined more precisely.

### 2.6 Elastic recursive parallelism

The architecture must not impose a fixed worker count. Work may recursively decompose into independent child work units and dispatch across available execution backends, subject to explicit budgets, authority, dependency barriers, collision controls, provider quotas, and termination rules.

The goal is elastic concurrency with no unnecessary central bottleneck, not literal infinite execution.

### 2.7 Shared mutable state is a controlled boundary

Independent work should prefer immutable inputs, isolated branches/worktrees, append-only receipts, content-addressed subjects, and compare-and-swap writes. Tasks that would collide on mutable state must serialize, partition, or stop rather than race blindly.

## 3. Scope

### In scope for V1

- project and repository registry
- dependency graph
- authority/capability declarations
- exact-head observation and drift detection
- derived currentness and review-validity state
- work-frontier generation
- priority ranking
- recursive work decomposition model
- execution backend abstraction
- GitHub Actions as the first execution backend
- task leasing and deduplication model
- collision detection
- budgets and recursion limits
- durable public-safe run receipts
- failure and retry classification
- workflow/test harness
- public-safe scheduled portfolio scans

### Explicitly out of scope for V1

- universal write credentials across all repositories
- automatic merge authority in downstream repositories
- production deployment authority in downstream projects
- autonomous credential creation or permission expansion
- private payload storage in this public repository
- arbitrary self-hosted runner execution for untrusted public pull requests
- claims of consciousness, independent agency, or unrestricted authority
- a requirement for a permanent server or edge service

## 4. Repository Layout

```text
project-runner/
├── README.md
├── PROJECT_RUNNER.md
├── pyproject.toml
├── registry/
│   ├── projects.yaml
│   ├── repositories.yaml
│   └── capabilities.yaml
├── topology/
│   ├── dependencies.yaml
│   └── shared-primitives.yaml
├── policy/
│   ├── authority.yaml
│   ├── currentness.yaml
│   ├── review-validity.yaml
│   ├── propagation.yaml
│   ├── scheduling.yaml
│   └── budgets.yaml
├── schemas/
│   ├── project.schema.json
│   ├── dependency.schema.json
│   ├── capability.schema.json
│   ├── frontier.schema.json
│   ├── work-unit.schema.json
│   ├── observation.schema.json
│   └── run-receipt.schema.json
├── runner/
│   ├── __init__.py
│   ├── models.py
│   ├── registry.py
│   ├── observe.py
│   ├── graph.py
│   ├── currentness.py
│   ├── propagate.py
│   ├── frontier.py
│   ├── prioritize.py
│   ├── decompose.py
│   ├── dispatch.py
│   ├── leases.py
│   ├── budgets.py
│   └── receipts.py
├── checks/
│   ├── stale_reviews.py
│   ├── dependency_drift.py
│   ├── unmirrored_prs.py
│   ├── unresolved_frontiers.py
│   └── governance_conflicts.py
├── runs/
│   └── .gitkeep
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   ├── architecture/
│   └── superpowers/specs/
└── .github/
    └── workflows/
        ├── test.yml
        ├── portfolio-scan.yml
        ├── dependency-scan.yml
        ├── stale-state-scan.yml
        └── scheduled-sweep.yml
```

## 5. Core Data Model

### 5.1 Project

A project record identifies an orchestration subject, its repositories/surfaces, and the permissions Project Runner currently has.

Required semantic fields:

- stable project id
- display name
- repository/surface locators
- public/private classification
- authority/capability declaration
- dependency selectors
- status derivation mode
- optional Bus coordination identity

Current project status should be derived where practical rather than maintained as freeform prose.

### 5.2 Dependency edge

A dependency edge is directional and typed.

Minimum fields:

- provider
- consumer
- dependency kind
- exact artifact or selector
- invalidation trigger
- required consumer reaction
- evidence used to resolve whether the edge was affected

Examples of reactions:

- `NO_ACTION`
- `INSPECT`
- `RETEST`
- `REREVIEW`
- `REQUALIFY`
- `BLOCK`

### 5.3 Capability

Capabilities describe what Project Runner may do to a target surface. Capabilities must be explicit and target-scoped.

Examples:

- read
- analyze
- propose
- create_branch
- write_branch
- open_pr
- comment
- request_review
- merge
- deploy

Possessing one capability never implies possession of stronger capabilities.

### 5.4 Observation

An observation records a fact gathered from an authoritative or declared evidence source.

Minimum fields:

- target
- evidence class/source
- exact subject
- observed value
- observed time
- observer implementation/version

Observations must distinguish current authoritative evidence from cached convenience metadata.

### 5.5 Frontier

A frontier is an actionable or blocked unit of project progress derived from evidence.

Minimum fields:

- project
- subject
- work type
- reason opened
- dependencies
- required capabilities
- collision key(s)
- estimated cost class
- priority inputs
- status

Expected statuses include:

- `READY`
- `WAITING_DEPENDENCY`
- `WAITING_AUTHORITY`
- `RUNNING`
- `VERIFYING`
- `COMPLETE`
- `FAILED_RETRYABLE`
- `FAILED_DETERMINISTIC`
- `OUTCOME_UNKNOWN`
- `SUPERSEDED`

### 5.6 Work unit

A work unit is an executable decomposition of a frontier.

Each work unit has:

- immutable work id
- parent frontier/work id
- exact input subjects
- requested operation
- required capabilities
- collision domain
- recursion depth
- budget allocation
- lease/ownership state
- expected outputs
- completion criteria

A worker may produce child work units only within inherited or narrower authority and budget ceilings.

### 5.7 Run receipt

A run receipt is evidence of orchestration activity, not downstream authority.

It should record:

- runner version/commit
- run id
- trigger
- exact inputs
- projects observed
- work units created
- work units skipped and why
- dispatch decisions
- completed/failed/unknown outcomes
- dependency invalidations
- budget consumption
- errors
- exact output digests/locators

Receipts stored in this public repository must be public-safe.

## 6. Recursive Execution Model

Project Runner's recursive execution tree follows these rules:

1. A frontier may be decomposed only if decomposition produces semantically distinct child work.
2. Child authority is the intersection of parent authority and target capability policy. Authority may narrow through delegation; it may never expand through recursion.
3. Child budgets come from the parent's remaining budget. Recursive spawning cannot create budget ex nihilo.
4. Work with overlapping collision keys must not execute concurrently unless the target declares the operations commutative or otherwise safe.
5. Equivalent work units deduplicate by stable work fingerprint.
6. A completed exact-subject result should be reused when still current rather than recomputed.
7. Recursion terminates when work is complete, blocked, deterministic-failed, superseded, budget-exhausted, depth-limited, or no further meaningful decomposition exists.

### Work fingerprint

The fingerprint should be derived from semantic inputs such as:

- operation type
- exact target subject
- relevant policy version
- dependency frontier
- requested verification level

It must not depend on incidental scheduling order.

## 7. Scheduling and Prioritization

V1 priority should be deterministic and explainable. Inputs may include:

- dependency fan-out
- number of blocked downstream frontiers
- staleness/currentness risk
- failure severity
- project-declared priority
- expected execution cost
- availability of required authority
- whether work is independently executable now

The runner must emit the reasons for ranking; it must not hide prioritization behind an opaque score alone.

A blocked high-priority frontier should not prevent unrelated executable work from advancing.

## 8. Budgets and Safety Limits

Recursive execution requires explicit resource controls.

V1 budget dimensions:

- max recursion depth
- max child work units per parent
- max active work units per run
- max retries per retry class
- max GitHub Actions jobs per orchestration run
- optional wall-clock/run-age ceiling
- optional provider-specific quota ceilings

Budget exhaustion produces a bounded frontier/result, not an infinite retry loop.

## 9. Failure Model

Failures are classified before retry decisions.

### Retryable/transient

Examples:

- temporary API failure
- rate limit
- provider timeout
- workflow infrastructure failure

Retry with bounded backoff and idempotency safeguards.

### Deterministic

Examples:

- schema invalid
- authorization denied
- invalid target/ref
- test failure caused by exact source

Do not blindly retry. Produce a repair/debug frontier.

### Ambiguous mutation

If a write may have happened but the response is uncertain, reconcile the exact target before retrying. Possible states:

- exact intended result observed -> reuse/verify
- divergent result observed -> conflict
- state remains unknowable -> `OUTCOME_UNKNOWN`

## 10. GitHub Actions Execution Backend

GitHub Actions is the first backend because the repository is public and standard hosted-runner workflows can provide useful disposable execution capacity.

V1 workflows:

- `test.yml`: unit/integration/schema checks
- `portfolio-scan.yml`: observe registered public-safe repository state and generate a derived frontier report
- `dependency-scan.yml`: evaluate dependency-edge currentness
- `stale-state-scan.yml`: detect stale reviews/receipts/currentness assertions
- `scheduled-sweep.yml`: periodic orchestration scan

Security defaults:

- least-privilege `GITHUB_TOKEN`
- read-only permissions unless a workflow specifically requires more
- no repository secrets committed to source
- third-party actions pinned to immutable commits when introduced
- no privileged execution of untrusted PR code
- no broad downstream write credential in V1
- no self-hosted runner attached for untrusted public pull-request execution

## 11. Cross-Repository Authority Model

Project Runner may know that a downstream action is useful without being authorized to perform it.

Every candidate effect is checked against:

1. requested operation
2. target repository/surface
3. exact subject/currentness
4. configured runner capability
5. target/project governance constraints
6. collision state
7. budget

If capability is absent, Runner should generate `WAITING_AUTHORITY` or a proposal/review request rather than silently escalating.

Project Runner's own repository is separately authorized for normal design, implementation, testing, maintenance, review, and integration under Patrick's 2026-09-17 repo-scoped authorization.

## 12. Data Flow

Nominal sweep:

```text
trigger
  -> load registry + policy
  -> observe exact subjects
  -> normalize observations
  -> compare with prior public-safe receipts/state
  -> update dependency graph currentness
  -> derive invalidations
  -> generate frontiers
  -> remove duplicate/currently-satisfied work
  -> partition by collision domain
  -> rank executable work
  -> allocate budgets
  -> dispatch independent work
  -> collect exact outputs
  -> verify completion criteria
  -> emit receipt
  -> derive next frontier
```

The loop may recurse, but each step remains bounded by authority, budgets, and exact evidence.

## 13. Derived State Versus Authoritative State

Project Runner should prefer reproducible derived state over mutable narrative state.

Examples:

- "PR head" comes from fresh GitHub evidence, not a hand-written pointer.
- "review stale" is derived from review subject versus current exact head.
- "consumer recheck required" is derived from changed provider artifact intersecting dependency selectors.
- "work complete" requires declared completion evidence, not merely a dispatched job.

Convenience indexes may exist, but they must be explicitly labeled non-authoritative and reconstructible.

## 14. Testing Strategy

Implementation follows test-driven development.

### Unit tests

- schema/model validation
- dependency intersection
- currentness calculation
- capability narrowing
- work fingerprint stability
- deduplication
- collision partitioning
- budget inheritance
- recursion termination
- priority explanation
- failure classification

### Integration tests

Use deterministic fixtures/fake repository observations to test:

- upstream change invalidates only matching consumers
- unchanged exact head does not trigger needless work
- stale review becomes `REREVIEW`
- blocked work does not stall unrelated work
- child worker cannot expand authority
- recursive decomposition respects depth and job budgets
- ambiguous mutation reconciles before retry
- duplicate work from multiple paths executes once

### Workflow tests

GitHub Actions definitions must be syntax-validated and exercise the Python test suite on pull requests and pushes.

## 15. Initial Population Strategy

V1 should begin with a deliberately small registry rather than importing every repository immediately.

Seed with:

1. `project-runner`
2. `chat-communication-bus`
3. `vera`
4. `vera-control-plane`
5. `hc-brain`

These are enough to exercise project registry, communication-surface distinction, provider/consumer edges, exact-head drift, review invalidation, and differing authority scopes.

Additional projects should be added only after the schema and scans work end-to-end on this seed set.

## 16. Initial Milestones

### M1 — Kernel

- repository scaffold
- schemas
- typed models
- registry loader
- validation tests

### M2 — Observation and graph

- exact-subject observations
- dependency graph
- currentness engine
- invalidation propagation

### M3 — Frontier engine

- frontier generation
- prioritization
- deduplication
- collision domains

### M4 — Recursive dispatcher

- work units
- capability inheritance
- budgets
- leases
- recursive decomposition
- local/mock execution backend

### M5 — GitHub Actions backend

- CI
- portfolio scan
- dependency scan
- stale-state scan
- scheduled sweep
- public-safe receipt artifacts/state

### M6 — Seed portfolio integration

- populate five seed projects
- validate real-world observations
- generate first portfolio frontier
- hostile review of authority and recursion boundaries

## 17. Success Criteria for V1

V1 is successful when, from a clean run against the seed registry, Project Runner can:

1. identify exact current repository subjects;
2. detect a provider change;
3. determine which declared consumers are actually affected;
4. create deduplicated frontiers;
5. separate executable work from authority-blocked work;
6. safely fan independent work into multiple bounded work units;
7. prevent recursive authority expansion;
8. prevent duplicate/colliding work from racing incorrectly;
9. collect completion evidence and emit a public-safe receipt;
10. derive the next frontier without requiring a manually curated narrative status file.

No success claim for V1 implies universal cross-repository authority, production deployment, or unlimited physical concurrency.

## 18. Future Extensions — Not V1 Requirements

Possible later extensions include:

- GitHub App with narrowly scoped installation permissions
- multiple execution backends
- serverless/edge event receiver
- private companion state service
- dynamic provider quota discovery
- cost-aware dispatch
- signed/attested run receipts
- cross-run distributed leases
- formally modeled invariant-confluence for shared orchestration state
- richer Bus-driven work assignment
- recursive delegated review trees

These should be added only when concrete usage demonstrates the need.

## 19. Design Decision

Build Project Runner as a **public-safe, authority-bounded, exact-subject-aware, recursively parallel orchestration kernel**. Keep downstream repositories authoritative for their own state and protected effects. Maximize safe independent execution paths while minimizing shared mutable bottlenecks and stale narrative state.
