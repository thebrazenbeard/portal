> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# P.O.R.T.A.L.

**Portfolio Orchestration & Repository Tracking Access Layer**

P.O.R.T.A.L. exists so a **single P.O.R.T.A.L. chat** can coordinate and advance the **whole portfolio** in maximal safe parallelism.

The intended operating model is not one chat manually working repositories in sequence. Vera acts as the cross-project coordinator; P.O.R.T.A.L. discovers and schedules executable frontiers across all active projects; the inherited Project Runner kernel claims, fences, routes, executes, verifies, and reconciles work; independent lanes run in parallel; and every freed lane is refilled with the next eligible frontier.

See [Single-Chat Whole-Portfolio Architecture](docs/architecture/PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md).

## Architecture

- `runner/` is the inherited Project Runner execution engine: exact-subject identity, portfolio cycles, durable queues, budgets, leases, fencing, worker routing, receipts, reconciliation, supervised tasks, and completion verification.
- `portal/` is the P.O.R.T.A.L. portfolio composition layer: whole-portfolio coordination, node placement, and the future single-command continuous run surface.
- Vera owns cross-project coordination and collision/ownership arbitration.
- Durable state lives outside chat context so a fresh P.O.R.T.A.L. chat can reconstruct and resume the portfolio.
- Node placement and schedulability do not create protected-effect authority.

P.O.R.T.A.L. was seeded from Project Runner because the required execution loops largely already exist. The architectural task is to compose them into one whole-portfolio control surface, not to reinvent a second runner.

## Current implementation state

The current `portal/` package implements deterministic wave-to-node planning and a `portal plan` CLI. That is the **current implementation slice**, not the mission ceiling.

The next build target is `portal run`: compose Project Runner's existing `portfolio-cycle`, `consume-queue`, claim/fence, worker-route, receipt/reconciliation, and task-supervision machinery with resident-loop/recovery mechanisms mined from Patrick's own repositories.

## Internal-first donor policy

Before implementing new orchestration machinery, Portal mines Patrick-owned repositories for existing qualified mechanisms. External projects are secondary research inputs.

See [Loop Donor Mining V1](docs/research/PORTAL_LOOP_DONOR_MINING_V1.md).

Primary internal donors currently include Project Runner, Pre-Active, WIP, CCB Base, Intranel, VeraMesh, WorkBridge Commander, WorkBridgeMCP, Vera Mono, Discovery, DriftGuard, Ingest, and Temporal.

## Quick start for the implemented planning slice

    python -m pip install -e '.[dev]'
    project-runner validate
    python -m pytest -q

Create a node manifest:

    schema: PORTAL_EXECUTION_NODES_V1
    nodes:
      - id: lappy
        max_parallel: 2
        allowed_lanes: []
        enabled: true
      - id: worklaptop
        max_parallel: 2
        allowed_lanes: []
        enabled: true

Plan a bounded portfolio wave:

    portal plan \
      --wave portfolio/advancement_wave.public.json \
      --nodes tests/fixtures/portal-nodes-valid.yaml \
      --max-parallel 4 \
      --max-per-lane 2 \
      --max-per-identity 2 \
      --max-per-family 2

The current command emits a deterministic JSON plan. It does not yet perform the whole-portfolio continuous run described above.

## Core invariants

- observation is not authority;
- coordination is not authorization;
- exact evidence outranks convenience pointers;
- one colliding semantic mutation subject has one mutation owner;
- blocked work does not stall unrelated executable work;
- node/tool availability does not manufacture authority;
- stale or ambiguous effects fail closed;
- worker success is not completion until required currentness/effect evidence verifies it;
- fresh-chat recovery comes from durable evidence, not assumed conversational continuity.

## Repository provenance

This repository was bootstrapped from:

- source repository: `thebrazenbeard/project-runner`
- source commit: `04702abbf51aa2920b7d054275619253ea6fa748`
- source tree: `f3f228258bc2de73724c598721bd5aa5beedab95`

The machine-readable binding is in [`.portal-bootstrap.json`](.portal-bootstrap.json).

Project Runner's inherited operator documentation remains in [`PROJECT_RUNNER.md`](PROJECT_RUNNER.md). P.O.R.T.A.L.'s coordination contract is in [`PORTAL.md`](PORTAL.md).
