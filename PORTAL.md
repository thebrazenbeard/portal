# P.O.R.T.A.L. Coordination Contract

**P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking Access Layer**

P.O.R.T.A.L. is designed so a **single P.O.R.T.A.L. chat** can coordinate and advance the **whole portfolio** through independent **parallel** execution lanes.

Project Runner remains the governed execution substrate. P.O.R.T.A.L. composes its currentness, collision, budget, authority, queue, worker-routing, fencing, task-supervision, receipt, and verification semantics rather than replacing them.

## Mission and implemented coordinator core

Mission:
- whole accessible active portfolio;
- single chat control surface;
- Vera cross-project coordination;
- maximal safe parallel waves;
- continuous lane refill;
- durable recovery across chat/runtime loss.

Implemented coordinator core:
- live repository discovery with private membership kept operator-local;
- deterministic Project Runner wave admission and execution-node placement;
- exact-head claims, leases, fencing, durable receipts and reconciliation;
- zero-mutation advisory workers for source-proposal generation;
- hash-bound multi-file source-tree proposals;
- separate Project Runner review, execution-authority and protected-effect promotion;
- atomic exact-head multi-file GitHub source publication;
- independent effect verification and ambiguous-outcome reconciliation;
- durable parent ecosystem sessions that exclude previously admitted subjects and refill repository lanes across successive child waves;
- CLI surfaces for discovery, bounded runs, wave preparation, worker delivery, source-proposal promotion/execution/reconciliation, ecosystem refill and status.

This is no longer a planning-only implementation. Remaining work before a complete whole-ecosystem claim is concentrated in qualified intelligent proposal authorship for arbitrary repositories, first-class multi-repository workstream execution, and live node/backend qualification at run time.

## Existing execution-loop substrate

The inherited `runner/` already exposes the critical loop primitives:
- `portfolio-cycle`;
- `portfolio-wave-plan`;
- `portfolio-wave-claim`;
- `consume-queue`;
- `claim-worker-route`;
- `record-worker-receipt`;
- `reconcile-queue`;
- `task-start` / `task-status` / `task-finalize` / `task-reconcile`.

The next Portal implementation should compose those mechanisms with resident-loop, checkpoint/recovery, bus, and multi-device mechanisms already present elsewhere in Patrick's portfolio.

## Single-chat control boundary

The single P.O.R.T.A.L. chat is an operator/control surface, not canonical persistence.

A fresh chat must be able to reconstruct the run from durable state and continue without requiring the prior transcript.

Vera coordinates project/lane ownership and collisions. Portal schedules. Project Runner executes/fences/verifies. Execution backends are bound per node and must be independently current and authorized. Capability is never inferred across transports. In the current workstation topology, WorkLaptop is an Executor-backed node only; WorkBridge/Desktop Commander capability must not be inferred for it.

## Whole-portfolio loop

The normative target is:

`DISCOVER -> REFRESH -> ADMIT -> ASSIGN -> CLAIM -> EXECUTE -> VERIFY -> PERSIST -> REFILL -> REPEAT`

A blocked project releases scheduling capacity to unrelated eligible work.

The loop stops only when:
- no executable frontier remains;
- configured budget/time/capacity ceilings stop it;
- a required authority/currentness dependency blocks all remaining work;
- Patrick or Vera intentionally stops/holds the run.

## Node and lane rule

Execution nodes declare capacity and lane eligibility. Lane placement never widens effect authority.

Each lane binds an exact subject, owner, collision domain, authority ceiling, backend/node, and verification contract.

One exact semantic mutation subject has one mutation owner at a time.

## Recovery/currentness rule

Before any resumed dispatch, Portal must refresh:
- exact target currentness;
- ownership/fence state;
- unresolved/ambiguous effects;
- dependency/collision state;
- node/backend availability;
- relevant authority.

A prior plan or chat-local statement is not sufficient currentness evidence.

## Donor-mining rule

Portal must mine Patrick-owned repositories before adopting external mechanisms. See:
- `docs/research/PORTAL_LOOP_DONOR_MINING_V1.md`
- `docs/architecture/PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md`

## Authority ceiling

P.O.R.T.A.L. scheduling does not grant execution authority.

Whole-portfolio operation does not imply merge, deploy, install, provider mutation, credential change, spend, protected evaluation-bank access, canonical-memory mutation, publication, or other protected effects.

`SCHEDULABLE != AUTHORIZED`

`DISPATCHED != VERIFIED EFFECT`

`WORKER RECEIPT != COMPLETION WITHOUT REQUIRED READBACK`

## Bootstrap provenance

The inherited execution substrate was copied from:
- `thebrazenbeard/project-runner`
- commit `04702abbf51aa2920b7d054275619253ea6fa748`
- tree `f3f228258bc2de73724c598721bd5aa5beedab95`
