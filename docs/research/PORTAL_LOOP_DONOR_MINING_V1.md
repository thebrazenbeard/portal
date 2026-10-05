# P.O.R.T.A.L. Loop Donor Mining V1

Status: ACTIVE ARCHITECTURE RESEARCH
Date: 2026-10-05

## Governing rule

P.O.R.T.A.L. must **mine Patrick-owned repositories before adopting external mechanisms**.

The portfolio already contains durable execution, recovery, transport, queueing, checkpoint, verification, and autonomous-loop machinery. P.O.R.T.A.L. should compose proven mechanisms and preserve their boundaries instead of creating a parallel implementation lineage without need.

External repositories are used to challenge and improve the internal design, not to displace it automatically.

## Patrick-owned primary donors

### project-runner

Primary portfolio execution engine.

Reusable mechanisms:
- portfolio currentness cycle;
- durable frontier/queue state;
- collision and budget admission;
- exact-subject claims;
- leases/fencing;
- worker routing;
- worker receipts;
- ambiguous-outcome reconciliation;
- recursive work;
- background task supervision.

Portal action: compose directly; do not reimplement.

### pre-active

Primary resident/autonomous loop donor.

Reusable mechanisms:
- resident daemon polling;
- durable SQLite/WAL event queue;
- claim/reclaim fencing tokens;
- lease heartbeats;
- bounded retry/dead-letter/redrive;
- schedules and autonomous turns;
- durable runs/transcripts;
- bounded autonomous-turn budgets;
- pause/resume/cancel;
- model/tool decision fencing;
- mutation ledger and exact-result replay;
- BLOCKED_EFFECT and explicit reconciliation;
- append-only lifecycle journal.

Portal action: reuse its resident-loop/recovery patterns for continuous portfolio refill rather than inventing a new daemon model.

### wip

Primary chat/runtime-loss recovery donor.

Reusable mechanisms:
- append-only checkpoints;
- small HEAD/RESUME recovery projections;
- periodic checkpoint heartbeat;
- PREPARED -> ATTEMPTED -> VERIFIED/FAILED/AMBIGUOUS effect journal;
- optimistic generation/CAS;
- explicit do-not-repeat state;
- recovery from evidence rather than transcript replay.

Portal action: use WIP-style checkpointing for P.O.R.T.A.L. chat/run recovery and unresolved effects.

### ccb-core + chat-communication-bus

Primary durable coordination/bus donor.

Reusable mechanisms:
- strict taxonomy admission;
- DLQ;
- node registration and leases;
- subscriptions/routing;
- priorities 0-4;
- immediate priority-0 dispatch;
- deterministic duplicate suppression;
- heartbeat/accounting;
- append-safe projection/reconciliation;
- private overlay separation.

Portal action: use as durable coordination/event substrate where the active Bus contract permits.

### intranel

Primary machine-message contract donor.

Reusable mechanisms:
- deterministic message fields;
- origin/actor/target/reply separation;
- operation correlation;
- exact subject/effect/security fields;
- immutable canonical payloads;
- content identity;
- dedupe/idempotency;
- receipts that do not self-prove effects.

Portal action: use typed envelopes for cross-lane dispatch/receipt semantics rather than prose-only worker handoffs.

### vera-mesh

Primary multi-device execution-routing donor.

Reusable mechanisms:
- persistent authenticated sessions;
- multiplexed concurrent lanes;
- capability narrowing;
- collision claims;
- expiring leases;
- durable fencing/idempotency;
- DIRECT_STREAM / EDGE_STREAM / DURABLE_RELAY path classes;
- distinction between relay receipt and recipient completion.

Portal action: use for execution-node transport/routing when current and qualified.

### workbridgecommander

Primary workstation concurrency donor.

Reusable mechanisms:
- separate upstream-context and effect concurrency;
- qualified default floors of 32 upstream contexts and 4 workstation-effect lanes per device;
- explicit lane target/operation identity;
- authenticated device attachment;
- configurable capacity reporting.

Portal action: feed live node capacity into Portal scheduling; do not equate context capacity with mutation authority.

### WorkBridgeMCP

Primary bounded workstation-effect donor.

Reusable mechanisms:
- config-as-capability boundary;
- bounded filesystem roots;
- explicit executable grants;
- executable identity verification;
- reduced environment;
- no shell insertion in bounded native mode.

Portal action: preserve as an effect-admission boundary for nodes using bounded WorkBridge.

### vera-mono

Primary composition-root/effect-integrity donor.

Reusable mechanisms:
- one qualified host-composed runtime boundary;
- durable state directory;
- persistent effect fence;
- outbound trust/currentness ledger;
- crash/restart execution bindings;
- request/result/reconciliation digest cross-checks;
- separate install, route, runtime-consumption, and behavior verification surfaces.

Portal action: borrow composition-root and effect-state separation patterns; do not collapse source/build/install/runtime/effect claims.

### discovery

Primary whole-portfolio discovery donor.

Reusable mechanisms:
- whole accessible portfolio census;
- DISCOVER -> CLASSIFY -> IMPLEMENT -> VERIFY -> HAND OFF loop;
- currentness watcher;
- exact-head/tree membership evidence;
- stale evidence as review trigger, not mutation authority.

Portal action: use Discovery-style estate enumeration/classification for whole-portfolio intake.

### driftguard

Primary deterministic admission donor.

Reusable mechanisms:
- explicit policy;
- PASS/WARN/BLOCK/UNKNOWN;
- exact evidence/policy/result digest;
- deterministic regression gating.

Portal action: use for optional quality/admission gates before promotion or continued rollout.

### ingest

Primary intake/provenance donor.

Reusable mechanisms:
- exact acquisition;
- content-addressed identity;
- raw-versus-normalized separation;
- provenance-bearing receipts;
- bounded source adapters.

Portal action: use its discipline for evidence ingestion when Portal needs external artifact intake.

### temporal

Primary chronology donor.

Reusable mechanisms:
- append-only timestamped events;
- stable event IDs;
- deterministic ordering;
- elapsed-time arithmetic without semantic promotion.

Portal action: use for chronology where timing matters; do not make Temporal portfolio authority.

## Additional Patrick-owned donors to continue mining

The mining pass remains open across the full owned estate. High-priority additional surfaces include:
- `vera`
- `vera-control-plane`
- `bt2`
- `rezon`
- `hephaestus`
- `executor`
- `workbridge`
- `RepairTracker`
- `fuckup`
- `meso-crct`
- other active repositories whose source trees expose scheduler, queue, lease, checkpoint, recovery, worker, daemon, orchestration, or verification mechanisms.

Repository names or README claims alone do not qualify a donor mechanism. Exact source and tests must be inspected before direct adoption.

## External reference repositories supplied by Patrick

### AMAP-ML/LongHorizon-Harness

High-value reference.

Useful patterns:
- explicit plan -> act -> verify -> checkpoint/recover -> repeat loop;
- Manager / Executor / independent Auditor role separation;
- fresh execution context per bounded action;
- only independently verified progress becomes trusted state;
- original goal + verified state survive context refresh;
- multiple agent backends behind adapters;
- conversational follow-up on the same durable run.

Potential Portal adoption:
- manager/executor/auditor separation at lane level;
- verified-checkpoint-only advancement;
- fresh-context worker rounds;
- run ledger supporting follow-up from one Portal chat.

### LarsCowe/bmalph

High-value reference for plan-to-autonomous-implementation handoff.

Useful patterns:
- explicit planning phases before implementation;
- conversion of planning artifacts into an executable fix plan;
- autonomous Ralph loop;
- story-by-story execution;
- TDD/commit progression;
- circuit breaker;
- incremental plan merge preserving completed work.

Potential Portal adoption:
- convert durable project frontiers into executable lane plans;
- preserve completed work across replans;
- circuit-break runaway lane execution.

### damonwan1/AutoScholarLoop

Useful nested-loop reference.

Useful patterns:
- role/stage separation;
- explicit checkpoint artifacts;
- nested decision, execution-review, writing-review, quality-gate loops;
- terminal revise/pivot/kill outcomes.

Potential Portal adoption:
- nested subloops inside a project lane;
- stage-specific verification artifacts;
- explicit pivot/kill/hold outcomes rather than endless retries.

### inferablehq/inferable

High-value durable-workflow reference.

Useful patterns:
- versioned long-running workflows;
- version affinity for in-flight executions;
- human approval interrupts;
- structured output validation/retries;
- memoized side-effect/expensive-operation results;
- long polling into private infrastructure;
- timeline observability.

Potential Portal adoption:
- workflow-version binding for in-flight lanes;
- Patrick approval interrupt as a first-class durable state;
- memoization/reuse of verified exact effects;
- node pull/long-poll model where useful.

### ray-r-ren/agent-apprenticeship

Useful iterative-agent-loop reference.

Useful patterns:
- apprentice + mentor/human review;
- bounded maximum loop depth;
- watch/status UX;
- reusable execution experience;
- configurable autonomous/expert-led modes.

Potential Portal adoption:
- review/mentor role as an optional lane gate;
- explicit loop-depth limits;
- status/watch UX;
- reusable post-run lessons without confusing them with current authority.

### vxcozy/workflow-orchestration

Useful lightweight discipline reference.

Useful patterns:
- plan before non-trivial work;
- subagent delegation;
- verify before completion;
- replan on failed verification;
- capture lessons after corrections.

Potential Portal adoption:
- simple operator-facing workflow discipline around deeper Project Runner mechanics.

### vault-developer/event-loop-explorer

Low direct code relevance to agent orchestration.

It visualizes JavaScript event-loop mechanics rather than providing a durable agent execution system.

Potential Portal value:
- conceptual visualization vocabulary for ready queues, microtasks/high-priority work, active stack, asynchronous completions, and render/status phases.

Do not treat it as an orchestration runtime donor merely because its name contains "event loop."

## Selection rule

A donor mechanism is adopted only when it:
1. solves a concrete Portal gap;
2. preserves exact-subject/currentness and authority boundaries;
3. is simpler than creating or retaining duplicate logic;
4. has an explicit recovery/rollback story;
5. is testable in isolation;
6. survives comparison against existing Patrick-owned mechanisms.

`INTERNAL_REUSE > EXTERNAL_IMPORT` when capability and evidence are otherwise comparable.

`REFERENCE != DEPENDENCY`

`DONOR_PATTERN != RUNTIME_AUTHORITY`
