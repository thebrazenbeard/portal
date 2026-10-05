> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# P.O.R.T.A.L.

**Portfolio Orchestration & Repository Tracking Access Layer**

P.O.R.T.A.L. is the portfolio-level coordination layer for a multi-repository project ecosystem. It answers a broader question than Project Runner alone: given all currently known projects, dependencies, ownership boundaries, collisions, worker lanes, and execution nodes, what independent work can safely advance now and where should it be routed?

P.O.R.T.A.L. was seeded from the Project Runner execution kernel and deliberately keeps that kernel intact.

## Architecture

- `runner/` is the inherited Project Runner substrate: exact-subject identity, currentness, dependency propagation, budgets, leases, fencing, queue state, worker routing, GitHub execution gates, and completion verification.
- `portal/` is the P.O.R.T.A.L. coordination layer: portfolio-wide planning and execution-node placement.
- P.O.R.T.A.L. consumes Project Runner admission decisions rather than replacing them.
- Node placement is scheduling metadata. It does not create repository or provider authority.

The first Portal slice is intentionally planning-only. It can assign already-admitted work across declared execution nodes while respecting global Project Runner collision and budget decisions. It does not yet perform live multi-machine dispatch.

## Quick start

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

The command emits a deterministic JSON plan. It performs no execution.

## Core invariants

P.O.R.T.A.L. inherits and preserves Project Runner's safety model:

- observation is not authority;
- coordination is not authorization;
- exact evidence outranks convenience pointers;
- one colliding semantic subject is not admitted twice;
- blocked work does not stall unrelated work;
- node/tool availability does not manufacture authority;
- stale or ambiguous effects fail closed;
- worker success is not completion until required currentness/effect evidence verifies it.

Portal adds one additional rule: machine placement happens only after Project Runner admission. A node cannot make otherwise-ineligible work runnable merely because it has capacity.

## Repository provenance

This repository was bootstrapped from:

- source repository: `thebrazenbeard/project-runner`
- source commit: `04702abbf51aa2920b7d054275619253ea6fa748`
- source tree: `f3f228258bc2de73724c598721bd5aa5beedab95`

The machine-readable binding is in [`.portal-bootstrap.json`](.portal-bootstrap.json).

Project Runner's inherited operator documentation remains in [`PROJECT_RUNNER.md`](PROJECT_RUNNER.md). P.O.R.T.A.L.'s coordination contract is in [`PORTAL.md`](PORTAL.md).
