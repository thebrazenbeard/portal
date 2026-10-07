# P.O.R.T.A.L. Portfolio Coordinator Design

**P.O.R.T.A.L.** means **Portfolio Orchestration & Repository Tracking Access Layer**.

## Purpose

P.O.R.T.A.L. is the whole-portfolio coordinator built around a single P.O.R.T.A.L. chat control surface.

The mission is to keep every eligible active project advancing in maximal safe parallelism. The chat is not a sequential project worker; it is the operator surface for a durable portfolio scheduler. Vera coordinates cross-project ownership/collisions. P.O.R.T.A.L. schedules lanes and nodes. Project Runner remains the governed execution engine.

The repository was seeded from `thebrazenbeard/project-runner@04702abbf51aa2920b7d054275619253ea6fa748`.

## Architecture

Project Runner already contains durable portfolio and execution-loop mechanisms. Portal therefore composes rather than reinvents:
- durable portfolio currentness;
- queue consumption;
- exact claims/fencing;
- worker routing;
- receipts and reconciliation;
- recursive work and budgets;
- task supervision.

The full target architecture is defined in:
`docs/architecture/PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md`.

Donor/reuse analysis is defined in:
`docs/research/PORTAL_LOOP_DONOR_MINING_V1.md`.

## Current first slice

The first Portal package adds deterministic node assignment above Project Runner wave admission.

Portal planning is two-stage:
1. Project Runner selects a deterministic collision-free, authority-ceiling-safe wave.
2. Portal assigns those already-admitted subjects to declared execution nodes without granting new authority.

An execution node declares:
- `node_id`;
- `max_parallel`;
- optional `allowed_lanes`;
- `enabled`.

For each admission, Portal chooses the eligible node with the lowest current load; ties break lexicographically by `node_id`.

## Whole-portfolio successor

The next implementation layer is not a new bespoke execution loop. It is a Portal composition root that drives:
`portfolio-cycle -> wave admission -> node placement -> exact claim -> queue/worker routing -> execution -> receipt/reconciliation -> refill`.

Resident/recovery behavior should reuse Patrick-owned mechanisms, especially Pre-Active and WIP, where their contracts fit.

## Failure behavior

Malformed manifests, stale currentness, collision conflicts, unsupported authority, unresolved ambiguous effects, and unavailable execution nodes fail closed.

A blocked subject does not block unrelated work.

## Initial claim ceiling

Current code proves deterministic portfolio-to-node planning only. The mission and architecture are broader, but live whole-portfolio execution is not claimed until the composition loop is implemented and qualified.
