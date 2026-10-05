# P.O.R.T.A.L. Chat Continuation — 2026-10-05T17:31-04:00

Schema: `PORTAL_CHAT_CONTINUATION_V1`

## Restore command

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261005T1731-0400`

## Repository binding

- repository: `thebrazenbeard/portal`
- branch: `work/portal-coordinator-v1`
- implementation head immediately before this checkpoint: `45c35744ec6161764052afe34226ed034b5ad291`
- Draft PR: `#1 — P.O.R.T.A.L. portfolio coordinator v1`
- PR state at checkpoint: open + draft
- merge/deploy authority: **NOT GRANTED**
- checkpoint files are state/continuation artifacts only; they do not authorize merge, deployment, provider mutation, credential changes, spend, or destructive rewrites.

## Current ownership / routing

Patrick reassigned implementation ownership back to the P.O.R.T.A.L. chat after the Work chat became rate-limited.

The Work chat:
- title: `Build P.O.R.T.A.L. coordinator`
- thread: `thread://01a10dc9-3926-79b3-8a0e-bb92c54da9f8?hostId=local`
- was explicitly allowed to collaborate/read this chat
- is **not** the current mutation owner after Patrick's reassignment
- do not assume it performed work beyond the live Git head.

The next P.O.R.T.A.L. chat becomes implementation owner when this restore command is intentionally invoked.

## Product identity and original purpose

**P.O.R.T.A.L. — Portfolio Orchestration & Repository Tracking Access Layer**

The original product goal remains the governing architecture:

> One P.O.R.T.A.L. chat coordinates the whole project/repository ecosystem. Patrick gives top-level commands for now; P.O.R.T.A.L. autonomously schedules, routes, verifies, and refills the work underneath those commands. Later, Vera/P.O.R.T.A.L. may generate those top-level commands itself through a separate policy layer.

The control plane must work from normal **ChatGPT web/HTML**, not only ChatGPT Desktop.

Desktop/local facilities are optional execution adapters. They must never be prerequisites for the canonical P.O.R.T.A.L. session.

## Client-independence invariant

The canonical control/session layer must not depend on:
- ChatGPT Desktop;
- Desktop Commander;
- a persistent local shell;
- local chat filesystem state;
- one particular workstation.

It must be callable from ChatGPT web/HTML using available connectors/tools and durable external state.

Execution capability is adapter-specific. A browser-hosted P.O.R.T.A.L. chat may coordinate work that executes elsewhere.

### WorkLaptop binding

At the last live topology observation supplied by Patrick:
- WorkLaptop generation: 9
- capacity: 8
- active: 0
- queued: 0
- **WorkLaptop is Executor-only.**

Do **not** infer WorkBridge/Desktop Commander capability for WorkLaptop.

## Correct autonomy model

### Now

Patrick issues a top-level command:
- `run`
- `continue`
- `hold`
- `complete`
- `status`
- `stop`

P.O.R.T.A.L. then autonomously:
1. refreshes portfolio/currentness;
2. reconciles existing work/effects;
3. computes the maximal safe parallel wave;
4. assigns free execution capacity;
5. routes work to existing qualified execution systems;
6. verifies results/effects through the owning substrate;
7. persists the new frontier;
8. refills freed lanes until the command's bounded objective is reached.

### Later

A Pre-Active/self-commanding policy may invoke the **same** command-session API. Do not build a second autonomous control plane.

## Smallest intended architecture

```text
Patrick / future self-command policy
               |
               v
     P.O.R.T.A.L. command session
               |
               v
 refresh currentness + reconcile
               |
               v
 Project Runner wave admission
(active / held / terminal state included)
               |
               v
 Portal execution-adapter routing
        /       |        \
       v        v         v
 Executor   Work chat   other qualified
 WorkLaptop   lane       substrate
       \        |         /
               v
 substrate-owned verification/effect evidence
               |
               v
 durable session projection + refill
```

**P.O.R.T.A.L. owns coordination state. Existing substrates own execution state.**

## Current implementation state at `45c35744...`

The branch already contains substantial useful machinery:

- Project Runner seeded into P.O.R.T.A.L.
- deterministic portfolio/wave scheduler
- collision/budget/identity/family/lane admission
- execution-node placement
- live repository discovery / local registry overlay
- durable Project Runner currentness/queue/claim/fence/result machinery
- Portal planning and bounded run code
- experimental ecosystem/proposal/worker machinery
- source-proposal/promotion/multi-file write experiments
- CLI surfaces around several of those layers
- extensive inherited Project Runner test coverage

### Active-lane accounting — important recent fix

The scheduler now has `active_subjects` support so already-running work consumes:
- global max-parallel capacity;
- per-identity capacity;
- per-family capacity;
- per-lane capacity;
- collision reservations.

Recent commits propagated that through the higher layers:

- `b078f974cd849c8e0e055e6b8ae7825c3142ccd5` — test active lane accounting
- `6d1540ff7bb1cac375b4e7ba61b21024fa8b7242` — scheduler active-lane accounting
- `aaaaff41781f828dab093062c2720c3186ef901e` — test Portal propagation
- `f0640a2cf8fc185ecb04e18c2eb6a8e83ff8afe0` — Portal planning propagation
- `fcde5ed5d8e67f026e54b38c6389e7674a5005c0` — canonical plan binding propagation
- `45c35744ec6161764052afe34226ed034b5ad291` — durable wave preparation propagation

Do not reimplement this feature from scratch; verify/read the live code first.

## CI evidence at checkpoint

For exact head `45c35744ec6161764052afe34226ed034b5ad291`:

- `test` workflow run #195: **success**
- `test` workflow run #194: **success**
- Dependency Review run #91: **success**
- PR #1 workflow run #96: **success**
- several `Code scanning AI findings on PR #1` runs reported failure, with one still in progress at the checkpoint. Treat those as unresolved workflow evidence until their logs/findings are inspected; do not silently reinterpret them as either code defects or quota noise.

No local WorkLaptop qualification result after the final active-subject propagation was captured into this checkpoint. Refresh if material.

## Course correction — what is core vs optional

The chat drifted by turning P.O.R.T.A.L. toward its own autonomous software-engineering platform. Patrick corrected this.

### Core

The core should remain small:

1. one durable command session;
2. portfolio/currentness refresh;
3. Project Runner admission/collision/budget semantics;
4. mapping active/held/completed subjects to free execution capacity;
5. explicit execution-adapter routing;
6. substrate-owned result/effect verification;
7. durable frontier/status projection;
8. lane refill.

### Demote to optional adapters / experimental implementation

Do **not** delete these immediately, but do not let them define the core:

- `portal/worker_backend.py`
- `portal/worker_registry.py`
- `portal/worker_runtime.py`
- `portal/source_proposal.py`
- proposal-specific sections of `portal/wave_runtime.py`
- source-proposal promotion/execution experiments
- ecosystem proposal-generation loop where it duplicates the command-session role

These may be retained behind optional execution adapters after the canonical command session works.

## Existing seams to reuse

### Project Runner — primary scheduler/execution semantics

Reuse rather than duplicate:
- portfolio/currentness collection;
- `plan_wave_admission()`;
- collision keys and budget enforcement;
- exact claims/fencing;
- queue consumption/reconciliation;
- durable dispatch/result verification;
- recursive work state;
- effect promotion where an execution backend genuinely needs it.

### Pre-Active — future self-command layer

Use later for:
- resident daemon/event loop;
- scheduled/autonomous command invocation;
- heartbeat/lease patterns;
- bounded autonomous turns.

Do not make it necessary for Patrick-commanded functionality.

### WIP

Use for:
- crash/chat recovery projections;
- do-not-repeat/ambiguous-effect recovery;
- compact resumable frontier state.

Do not create a second authoritative scheduler in WIP.

### CCB / chat-communication-bus

Use for:
- durable work-bearing cross-chat/lane coordination;
- replies/receipts/routing where the active Bus contract allows.

It is not the scheduler and does not manufacture authority.

### Executor

Current explicit WorkLaptop execution adapter.

WorkLaptop must be modeled as Executor-only until live evidence says otherwise.

## Concrete defect / architectural smell list

1. Multiple overlapping control planes exist:
   - `portal/runtime.py` run/cycle state;
   - `portal/ecosystem_runtime.py` ecosystem/generation state;
   - `portal/wave_runtime.py` packet/delivery/proposal state.

   The command-session work should establish **one canonical control projection**.

2. The CLI has grown lower-level wave/proposal commands but does not yet cleanly expose the six top-level product commands with one shared semantic state:
   - run
   - continue
   - hold
   - complete
   - status
   - stop

3. `complete` must mean **verify and close**. It must not blindly set a subject terminal because the user/worker said “done.”

4. `hold` is durable control intent. Held work must not be scheduled.

5. `stop` is a soft control-plane stop:
   - stop new admission/refill;
   - preserve active/unresolved work and evidence;
   - do not silently cancel in-flight external effects.
   Cancellation is a separate future operation.

6. Active subjects must be included in scheduling budgets/collisions. Recent commits were specifically intended to fix this across scheduler → Portal planning → canonical plan → durable wave prep.

7. Browser/HTML client support means the core must be a library/service contract, not CLI-first or Desktop-first.

## Recommended next implementation sequence

### Task A — verify the live active-subject propagation

Before editing:
1. refresh branch/PR;
2. inspect `runner/portfolio_wave_scheduler.py`;
3. inspect `portal/coordinator.py`;
4. inspect `runner/portfolio_plan_binding.py`;
5. inspect `portal/wave_runtime.py`;
6. run focused tests for active subject accounting.

Do not assume this checkpoint supersedes live code.

### Task B — canonical command session

Create a small core module, e.g. `portal/session.py`, with one durable session projection.

Suggested control states:
- `RUNNING`
- `STOPPED`

Suggested subject intents/states:
- `ACTIVE`
- `HELD`
- `TERMINAL`

Keep actual execution/result detail in the owning substrate, referenced by exact IDs/evidence.

Core methods should be library functions/classes usable equally by web tooling and CLI adapters:

- `run(...)`
- `continue_run(...)`
- `hold(...)`
- `complete(...)`
- `status(...)`
- `stop(...)`

### Task C — adapter interface

Add an explicit execution-adapter interface.

Minimum adapter responsibilities:
- capability/currentness read;
- dispatch/route one admitted subject;
- read/reconcile result;
- return exact durable evidence identifiers.

Initial real adapter:
- WorkLaptop via Executor.

Optional adapters later:
- Work chats
- Project Runner worker routes
- Pre-Active
- VeraMesh/WorkBridge on nodes where live topology actually qualifies them
- existing proposal/process worker machinery

### Task D — browser/HTML surface

Do not require a local CLI for product functionality.

The CLI should be one thin adapter to the session core.

ChatGPT HTML should be able to invoke the same session semantics through connected tools/connectors and durable state.

### Task E — tests

Red/green acceptance should include:

1. `run` fills maximal safe parallel capacity.
2. active subjects consume budgets and collision space.
3. `continue` refills only freed capacity.
4. `hold` prevents re-admission.
5. `complete` refuses without verification evidence.
6. verified completion frees capacity.
7. `stop` prevents new admission but preserves active work.
8. restart/fresh chat reconstructs session from durable state.
9. browser/client identity does not affect semantics.
10. WorkLaptop routes only through Executor.
11. unrelated blocked work does not stall safe work.
12. no protected effect authority is inferred from scheduling or adapter availability.

## What NOT to do on restore

- Do not merge PR #1 without Patrick's exact authority.
- Do not deploy.
- Do not reassign WorkLaptop to WorkBridge/Desktop Commander.
- Do not restart the architecture from scratch.
- Do not promote the proposal/worker experiments back into the canonical core without a concrete need.
- Do not treat CI queue/failure labels as semantic evidence without inspecting the actual run.
- Do not claim a Work chat/worker is active without durable evidence.
- Do not infer completion from worker success alone.
- Do not make Desktop a requirement.

## First restore cycle

On the next chat:

1. resolve this continuation file from the live repo;
2. refresh `work/portal-coordinator-v1` and PR #1;
3. compare live head against implementation head `45c35744...`;
4. if moved, inspect every intervening commit before mutation;
5. refresh CI and WorkLaptop Executor topology if material;
6. verify active-subject propagation;
7. build the canonical command-session surface;
8. test;
9. checkpoint again before the next context boundary.

## Exact next frontier

**Build the smallest durable, client-independent P.O.R.T.A.L. command session supporting Patrick-issued `run / continue / hold / complete / status / stop`, with autonomous scheduling/refill underneath those commands, reusing Project Runner execution semantics and treating existing worker/source-proposal machinery as optional adapters.**

