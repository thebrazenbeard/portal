# P.O.R.T.A.L. Portfolio Coordinator Design

**P.O.R.T.A.L.** means **Portfolio Orchestration & Repository Tracking Access Layer**.

## Purpose

P.O.R.T.A.L. is the portfolio-level coordinator above Project Runner. Project Runner remains the governed execution substrate: exact-subject identity, collision domains, durable currentness, leases/fencing, worker routing, bounded queue consumption, and verified execution remain in `runner/`. P.O.R.T.A.L. adds portfolio-wide node routing and coordination without weakening those controls.

The repository was seeded from `thebrazenbeard/project-runner@04702abbf51aa2920b7d054275619253ea6fa748`. The inherited runner code is not renamed or rewritten merely for branding.

## Architecture

The first Portal layer is a separate `portal/` package. It consumes the existing `AdvancementWave`, `WaveExecutionBudget`, and `WaveAdmissionPlan` interfaces from `runner.portfolio_advancement` and `runner.portfolio_wave_scheduler`.

Portal planning is two-stage:

1. Project Runner selects the deterministic collision-free, authority-ceiling-safe wave.
2. Portal assigns those already-admitted subjects to declared execution nodes without granting new authority.

An execution node declares:
- `node_id`
- `max_parallel`
- optional `allowed_lanes`
- `enabled`

Node assignment is deterministic. Eligible nodes are enabled, have remaining capacity, and either allow all lanes or include the admission's lane. For each admitted subject in Project Runner order, Portal chooses the eligible node with the lowest current load; ties break lexicographically by `node_id`. If no node is eligible, the subject is not dispatched and is reported as `NO_EXECUTION_NODE`.

Portal planning never:
- widens an effect ceiling;
- treats machine/tool availability as authority;
- resolves Project Runner HOLDs by itself;
- dispatches protected effects;
- claims worker success as verified completion;
- reassigns an already-owned semantic subject without an explicit coordinator decision.

## Interfaces

`portal.models.ExecutionNode`
- validates non-empty node IDs;
- requires positive integer capacity;
- normalizes allowed lanes into a deterministic tuple;
- disabled nodes are never eligible.

`portal.coordinator.plan_portal_wave(...)`
- consumes an `AdvancementWave`, a `WaveExecutionBudget`, declared nodes, and optional occupied collision keys;
- calls Project Runner's `plan_wave_admission` unchanged;
- produces assignments plus Portal-only deferrals;
- preserves the underlying Project Runner admission plan in the result.

`portal.cli`
- exposes `portal plan`;
- reads an advancement-wave JSON and a node-manifest YAML;
- emits a deterministic JSON plan;
- performs no execution in this first slice.

## Failure behavior

Malformed node manifests fail closed with a non-zero CLI exit. Duplicate node IDs are rejected. Zero or negative capacity is rejected. If all nodes are disabled or ineligible, Project Runner admissions remain visible but none are assigned. Existing Project Runner deferrals remain unchanged and separately visible.

## Testing

The first behavior test must fail before Portal production code exists. Focused tests cover deterministic balancing, lane eligibility, capacity exhaustion, duplicate nodes, disabled nodes, and preservation of Project Runner collision decisions. Integration coverage exercises the CLI against fixture files. The inherited Project Runner suite must remain green.

## Initial claim ceiling

This slice proves deterministic portfolio-to-node planning only. It does not prove live multi-machine dispatch, persistent node discovery, autonomous background operation, or protected-effect execution.
