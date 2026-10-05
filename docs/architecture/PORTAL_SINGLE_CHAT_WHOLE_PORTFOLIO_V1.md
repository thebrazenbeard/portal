# P.O.R.T.A.L. Single-Chat Whole-Portfolio Architecture V1

Status: MISSION-LEVEL ARCHITECTURE
Date: 2026-10-05

## Mission invariant

P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking Access Layer — exists so a **single P.O.R.T.A.L. chat** can coordinate and advance the **whole portfolio** in maximal safe parallelism.

The chat is not expected to manually visit repositories one by one. It is the human-facing control surface for a durable portfolio scheduler.

Target invariant:

`ONE_PORTAL_CHAT_CAN_ADVANCE_THE_WHOLE_PORTFOLIO_IN_MAXIMAL_SAFE_PARALLELISM`

This means:
- discover all active repository projects and current frontiers;
- partition executable work into independent parallel lanes;
- preserve dependency, collision, authority, review, and resource barriers;
- dispatch admitted work through governed execution backends;
- verify effects and completion independently;
- persist receipts/frontiers outside conversational context;
- immediately refill freed lanes with the next eligible work;
- continue until no executable frontier remains, a configured budget is exhausted, or Patrick/Vera intentionally stops the run.

It does **not** mean unlimited concurrency. Dependencies, shared mutable subjects, finite compute, review barriers, and authority gates still serialize work where serialization is actually required.

## Control-surface model

### Single P.O.R.T.A.L. chat

Patrick should be able to use one P.O.R.T.A.L. chat to:
- start or resume a portfolio-wide run;
- see active, blocked, verifying, and completed lanes;
- change portfolio priorities;
- stop or hold selected work;
- inspect why a project is blocked;
- continue after a fresh chat/runtime without reconstructing state from conversational memory.

The chat is an interface, not the canonical state store.

### Vera

Vera is the cross-project portfolio coordinator.

Vera owns:
- global dependency/collision reasoning;
- semantic-subject ownership arbitration;
- review-versus-implementation separation;
- priority/sequencing choices inside granted authority;
- HOLD/STOP routing when currentness, authority, or collision evidence is insufficient.

Vera coordination does not manufacture protected authority.

### P.O.R.T.A.L.

P.O.R.T.A.L. is the portfolio-level orchestration layer.

It owns:
- whole-portfolio discovery/intake;
- durable frontier aggregation;
- maximal safe wave selection;
- lane/node placement;
- continuous lane refill;
- run-level status/recovery;
- composition of existing execution loops and transports.

### Project Runner

Project Runner remains the governed execution engine embedded in this repository under `runner/`.

P.O.R.T.A.L. must compose Project Runner's existing durable primitives rather than replacing them with a second unrelated execution engine.

Existing primitives include:
- `portfolio-cycle` — refresh durable portfolio currentness/frontiers;
- `portfolio-wave-plan` — collision/budget-safe wave admission;
- `portfolio-wave-claim` — exact-subject claim/fencing;
- `consume-queue` — fenced ready-work consumption;
- `claim-worker-route` — durable worker-route delivery;
- `record-worker-receipt` — durable worker outcome evidence;
- `reconcile-queue` — explicit retry/terminal reconciliation;
- `task-start`, `task-status`, `task-finalize`, `task-reconcile` — supervised workstation tasks;
- recursive work, budgets, leases, fencing, and exact-effect verification.

## Whole-portfolio control loop

```text
Patrick -> single P.O.R.T.A.L. chat
                |
                v
          Vera coordination
                |
                v
    refresh authority/currentness
                |
                v
      discover whole portfolio
                |
                v
 Project Runner portfolio-cycle
                |
                v
   global dependency/collision map
                |
                v
  admit maximal safe parallel wave
                |
                v
 P.O.R.T.A.L. lane/node assignment
                |
                v
 exact claim/fence + worker routing
        /       |       |       \
       v        v       v        v
    lane A   lane B   lane C   lane N
       |        |       |        |
       +---- execute/verify -------+
                |
                v
       receipts + reconciliation
                |
                v
       durable frontier update
                |
                v
   refill every newly free safe lane
                |
                +---------- repeat
```

A blocked high-priority project must not stall unrelated executable work.

## Parallel-lane contract

Each independently executable lane binds at minimum:
- project/repository;
- exact ref/head or other exact subject;
- semantic work subject;
- mutation owner;
- reviewer identity/requirements when applicable;
- required capabilities;
- allowed and prohibited effects;
- dependency barriers;
- collision keys;
- execution node/backend;
- resource budget;
- verification contract;
- durable receipt destination;
- next frontier.

One exact semantic mutation subject has one mutation owner at a time. Multiple reviewers may inspect a frozen subject without becoming mutation co-owners.

Parallelism is elastic. Worker chats are not the unit of architecture; durable work subjects and execution lanes are.

## Durable state and fresh-chat recovery

The architecture must not depend on one chat remaining open.

Durable state comes from Project Runner state, repository state, Bus/CCB coordination, WIP/checkpoints, execution receipts, and other governed stores.

A fresh P.O.R.T.A.L. chat reconstructs:
- portfolio membership;
- current exact frontiers;
- lane ownership;
- unresolved effects;
- dependency/collision state;
- current execution-node availability;
- verification/review queues.

It then resumes from evidence.

## Composition rule

Before inventing a new orchestration mechanism, P.O.R.T.A.L. must mine Patrick-owned repositories for a qualified existing mechanism. External repositories are secondary research inputs.

The current internal donor map is recorded in:
`docs/research/PORTAL_LOOP_DONOR_MINING_V1.md`.

## Current implementation versus mission

The repository-level coordinator core is now implemented beyond planning:

- live portfolio repository discovery and local registry overlay;
- deterministic maximal-safe wave admission and node placement;
- exact-head Project Runner claims, leases and fencing;
- durable worker delivery/receipt state;
- zero-mutation advisory source-proposal workers;
- hash-bound multi-file source-tree proposals;
- separate Project Runner review/execution/effect promotion;
- atomic exact-head multi-file source writes;
- independent verification and ambiguous-effect reconciliation;
- durable parent ecosystem sessions that exclude previously admitted subjects and refill repository lanes across successive generations;
- operator CLI surfaces for ecosystem proposal advancement, status, proposal promotion, execution and reconciliation.

Current qualification has exercised the repository coordinator path on WorkLaptop through **Executor**, which is the only currently attached execution transport for that machine. P.O.R.T.A.L. must not infer WorkBridge/Desktop Commander capability for WorkLaptop.

The remaining mission gap is narrower: provide a qualified intelligent proposal-authoring backend that can do real arbitrary-repository work under the advisory packet contract, and add first-class execution for multi-repository workstream subjects. Until those are qualified, P.O.R.T.A.L. can coordinate and advance the repository estate structurally, but it cannot honestly claim autonomous semantic advancement of every project class.

## Protected-effect boundary

Whole-portfolio scheduling never implies merge, deploy, install, credential, permission, spend, protected-bank, canonical-memory, publication, or other protected-effect authority.

`SCHEDULABLE != AUTHORIZED`

`DISPATCHED != EFFECT_CONFIRMED`

`WORKER_SUCCESS != VERIFIED_COMPLETION`
