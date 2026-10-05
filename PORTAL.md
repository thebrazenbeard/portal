# P.O.R.T.A.L. Coordination Contract

**P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking Access Layer**

P.O.R.T.A.L. coordinates work across a portfolio. Project Runner remains the governed execution substrate. Portal composes Project Runner's existing currentness, collision, budget, authority, worker-routing, fencing, and verification semantics; it does not replace or silently weaken them.

## Boundary

The coordinator may determine that several independent pieces of work are simultaneously schedulable and may place those admitted subjects onto declared execution nodes. That placement does not grant execution authority. A machine being online, idle, technically capable, or authenticated does not make an otherwise gated effect authorized.

The initial implementation is planning-only: `portal plan` computes a plan but does not launch workers or mutate target repositories.

## Planning sequence

1. Load the portfolio advancement wave.
2. Apply Project Runner's deterministic wave admission.
3. Preserve every Project Runner deferral unchanged.
4. Consider only Project Runner-selected admissions for node placement.
5. Filter nodes by enabled state, remaining capacity, and lane eligibility.
6. Choose the eligible node with lowest current load; break ties by lexical node ID.
7. Emit `NO_EXECUTION_NODE` when an admitted subject has no eligible node.
8. Emit deterministic plan evidence.

Node assignment therefore cannot rescue a collision, scheduling hold, inert action, or budget deferral that Project Runner has already rejected.

## Execution node contract

Node manifests use `PORTAL_EXECUTION_NODES_V1`.

Each node declares:

- `id`: non-empty stable node identifier;
- `max_parallel`: positive integer placement capacity;
- `allowed_lanes`: optional lane allowlist; an empty list allows any lane;
- `enabled`: boolean, default true.

Duplicate node IDs, malformed manifests, empty lane names, and invalid capacities fail closed.

## Recovery and currentness

A Portal plan is evidence about the inputs from which it was computed. It is not a durable claim that those inputs remain current. Before a future dispatch layer executes a plan, it must rebind the underlying exact Project Runner subject, current ownership/fencing state, target authority, and live execution-node state.

A process restart, chat restart, or tool reconnection must not be treated as proof that prior machine availability, branch heads, leases, assignments, or provider state remain current.

## Bootstrap provenance

The inherited execution substrate was copied from:

- `thebrazenbeard/project-runner`
- commit `04702abbf51aa2920b7d054275619253ea6fa748`
- tree `f3f228258bc2de73724c598721bd5aa5beedab95`

The exact machine-readable binding is `.portal-bootstrap.json`.

## Current claim ceiling

Current Portal code proves deterministic Project Runner admission plus bounded execution-node placement and a planning CLI. It does not yet prove persistent node discovery, live Lappy/WorkLaptop dispatch, autonomous background scheduling, receipt ingestion from live workers, or protected-effect execution.
