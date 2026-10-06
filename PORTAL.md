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
- a durable command-session projection with `run`, `continue`, `hold`, `complete`, `status`, and `stop` semantics;
- verification-gated completion: a worker/process result never frees a subject until owning-substrate evidence verifies terminal or held state;
- resident bounded refill that reconciles active subjects, admits the next safe generation, and stops on verified idle, STOP, bounded waiting, or cycle limits;
- live occupied-node accounting so saturated/backlogged nodes are not treated as free merely because their declared maximum capacity is larger;
- a capability/currentness/authority route resolver plus composite execution router that binds exact targets to qualified adapters without hardcoding workstation or repository transports;
- durable bind-before-dispatch route evidence and adapter-owned reconciliation, including fail-closed handling of ambiguous outcomes and no silent route substitution;
- a built-in advisory process-proposal adapter that can be explicitly enabled from command-session `run` / `continue` with a worker-backend manifest, while retaining only `NO_PROTECTED_EFFECT` authority;
- CLI surfaces for discovery, command sessions, bounded runs, wave preparation, worker delivery, source-proposal promotion/execution/reconciliation, ecosystem refill and status.

This is no longer a planning-only implementation. The built-in command-session path can now dispatch configured advisory process-proposal work through capability-based routing. The remaining gap is broader live host qualification and additional concrete drivers for arbitrary semantic work (for example repo-native connectors, Executor, WorkBridge, or Lappy V2 routes supplied by the host); source presence alone is not an installed or continuously running P.O.R.T.A.L. runtime.

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

The command-session/refill layer composes those mechanisms rather than replacing them. Remaining execution-adapter work should bind qualified repository/workstation/service routes behind a small capability/evidence/reconciliation contract rather than adding another scheduler.

## Single-chat control boundary

The single P.O.R.T.A.L. chat is an operator/control surface, not canonical persistence.

A fresh chat must be able to reconstruct the run from durable state and continue without requiring the prior transcript.

Vera coordinates project/lane ownership and collisions. Portal schedules. Project Runner executes/fences/verifies. Execution routes are selected per target/effect from currently qualified repository, workstation, or service adapters. Discovery or connector presence is not capability, attachment, currentness, or authority. No workstation or repository is permanently married to one transport.

After dispatch, a timeout/disconnect or otherwise ambiguous effect remains bound to that attempted route/effect until reconciled. P.O.R.T.A.L. must not silently replay the same semantic effect through another adapter.

## Command-session semantics

The durable command session is operator intent, not a second execution engine:

- `run` starts/restarts the session and, by default, performs bounded reconcile/refill generations;
- `continue` admits the next safe generation around already-active work;
- `hold` records durable no-refill intent; already-dispatched work continues to occupy scheduling capacity until verified held/terminal evidence exists;
- `complete` independently verifies the owning wave delivery before moving a subject to terminal;
- `status` reconstructs control state, generation and subject state from SQLite rather than chat memory;
- `stop` prevents new admission/refill without cancelling or erasing active/unresolved effects.

`OUTCOME_UNKNOWN` and verification-stale work remains active/unresolved. It is never freed merely to make room for another attempt.

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

Execution nodes declare capacity and lane eligibility. Live occupied/backlogged slots are separate currentness evidence and reduce fresh placement capacity; declared maximum capacity alone does not make a node free. Lane placement never widens effect authority.

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
