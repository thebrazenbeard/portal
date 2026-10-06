# P.O.R.T.A.L. Chat Continuation — 2026-10-06 14:39 ET

**Schema:** `PORTAL_CHAT_CONTINUATION_V2`  
**Logical ID:** `PORTAL_CHAT_CONTINUATION_20261006T1439-0400`  
**Repository:** `thebrazenbeard/portal`  
**Working branch:** `work/portal-coordinator-v1`  
**Draft PR:** `#1 — P.O.R.T.A.L. portfolio coordinator v1`  
**Base:** `main@fa125a74bd42bc2bdabf3f8f9ef677b225db6f76`  
**Branch head immediately before this checkpoint:** `d4b1b0eddb6643795d505e56ef8f1c31160a94aa`  
**Implementation head beneath state-only continuation commits:** `618a5f63211b13d390b74d522734fca9bf743bbc`  
**Implementation-head message:** `docs: document atomic project route advertisement`  
**Checkpoint reason:** Patrick explicitly said this chat is full and ordered a durable repo continuation plus a restore command for a new chat.

## 1. Standing task

Patrick's standing instruction is:

> Resume and build the portal repo and populate it until it works like we want it to.

The intended result is one durable, client-independent P.O.R.T.A.L. coordination surface that can:

- refresh current portfolio/currentness;
- compute maximal safe parallel work;
- account for already-active and held subjects;
- account for live occupied capacity rather than only declared capacity;
- select an exact qualified execution route for each admitted subject;
- bind that route durably before any attempted effect;
- dispatch through existing execution substrates rather than replacing them;
- reconcile effects through the owning substrate;
- keep ambiguous attempted effects active and route-pinned;
- release capacity only after verified completion/held evidence;
- refill freed capacity automatically;
- survive chat/process loss through durable state;
- provide clean top-level `run / continue / hold / complete / status / stop` semantics;
- work from ordinary ChatGPT web/HTML, with Desktop/local routes optional rather than required.

Do **not** restart architecture.

## 2. Current exact source state

Live PR read immediately before this checkpoint:

- PR #1: **OPEN + DRAFT**
- branch: `work/portal-coordinator-v1`
- head: `d4b1b0eddb6643795d505e56ef8f1c31160a94aa`
- base: `main@fa125a74bd42bc2bdabf3f8f9ef677b225db6f76`

The branch is two commits ahead of implementation head `618a5f63211b13d390b74d522734fca9bf743bbc`, and those two commits only add the prior continuation pair:

- `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1351-0400.md`
- `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1351-0400.json`

Therefore the current implementation subject remains `618a5f63211b13d390b74d522734fca9bf743bbc`; the later commits are state/checkpoint only.

Exact-head CI at `d4b1b0eddb6643795d505e56ef8f1c31160a94aa`:

- GitHub Actions `test`: **SUCCESS**
- pytest: **672 passed in 27.08s**
- Dependency Review: **SUCCESS**

This is source/test evidence only. It does **not** prove install, resident runtime, current route, active host consumption, production effect, or portfolio closure.

## 3. Current implemented coordinator core

The current branch already contains the main source-level architecture we want, including:

- durable command-session projection;
- `run / continue / hold / complete / status / stop`;
- bounded resident reconcile/refill;
- active/held/terminal projection;
- verification-gated capacity release;
- active-subject collision/budget propagation;
- live occupied-node accounting;
- capability/currentness/authority-based route resolution;
- composite execution routing;
- durable bind-before-dispatch route evidence;
- adapter-owned reconciliation;
- no silent route substitution after effect attempt;
- `OUTCOME_UNKNOWN` remains active and route-pinned;
- external host bridge with route/occupancy/frontier currentness;
- atomic host dispatch taking and attempt-time route revalidation;
- host pump;
- host driver registry and command-backed host driver support;
- semantic frontier currentness;
- Project Runner task currentness;
- live portfolio membership/currentness;
- diagnostics;
- exact project-level route advertisement;
- closed-loop source-level acceptance coverage.

Important current files include:

- `PORTAL.md`
- `portal/session.py`
- `portal/route_resolver.py`
- `portal/execution_router.py`
- `portal/host_bridge.py`
- `portal/host_pump.py`
- `portal/host_command_driver.py`
- `portal/host_driver_registry.py`
- `portal/frontier_currentness.py`
- `portal/task_currentness.py`
- `portal/live_portfolio.py`
- `portal/process_adapter.py`
- `portal/diagnostics.py`
- `docs/operations/PORTAL_HOST_BRIDGE_RUNBOOK_V1.md`

## 4. Core invariants

Preserve these:

1. **P.O.R.T.A.L. owns coordination state; execution substrates own execution/effect state.**
2. Active subjects consume collision and scheduling budgets.
3. Worker/process success is not verified completion.
4. Completion/freeing capacity requires owning-substrate verification.
5. Bind exact route durably before effect attempt.
6. After an attempted ambiguous effect, keep the subject route-pinned until reconciliation.
7. No silent failover/substitution after the attempt boundary.
8. Availability != attachment != currentness != technical capability != effect authority.
9. Declared max capacity != live free capacity.
10. `hold` is durable no-refill intent, not cancellation authority.
11. `stop` prevents new admission/refill while preserving active/unresolved work.
12. Source/test PASS != install != registration != current route != runtime consumption != effect closure.
13. Explicit route selection fails closed if that exact route is not currently qualified.
14. Plugin/workstation discovery never manufactures authority.

## 5. Correct adapter model

Do not restore the obsolete rule “WorkLaptop routes only Executor.”

Execution routing is target-bound and capability-based. Candidate routes may include, only when actually live/current/attached/capable/authorized for the exact work:

- repo-native GitHub/plugin routes;
- WorkBridge Commander;
- WorkBridge Relay where it exposes the required capability;
- Executor;
- Lappy Desktop Commander V2;
- other future service-native/plugin adapters.

Core P.O.R.T.A.L. should not understand every provider directly. Adapter-specific details belong behind capability/evidence/reconciliation contracts.

One subject may use more than one substrate across phases, but once an effect attempt is bound to a route, ambiguous outcome must be reconciled before any substitute route is allowed.

## 6. Existing operational host sequence

The current runbook uses:

`REFRESH -> ADVERTISE -> INSPECT -> ADMIT -> BIND -> QUEUE -> TAKE/ATTEMPT-MARK -> EFFECT -> VERIFY -> RECONCILE -> REFILL`

Relevant CLI surfaces include:

- `portal status --session-id ... --host-details`
- `portal host unresolved`
- `portal host pending`
- `portal host occupancy`
- `portal host occupancy-status`
- `portal host advertise`
- `portal host advertise-projects`
- `portal host routes`
- `portal host take`
- `portal host attempt`
- `portal host reconcile`
- `portal run --session-id ... --host-bridge ...`
- `portal continue --session-id ... --host-bridge ...`

Queued work alone does not authorize an effect. The durable attempt marker must precede the effect call.

## 7. Stale-work evidence from the originating work

Historical observation from the preceding development chat: one Lappy Project Runner status read showed many stale/orphaned registrations and only a small number of actually running subjects. Code inspection established that task-currentness can distinguish process-gone/PID-reused records and can reconcile dead registrations to `UNKNOWN_EXIT` without killing/replaying processes.

This is **historical evidence only**. Fresh-read live task/currentness state before any present claim or cleanup attempt.

Do not “solve” stale workload by blindly killing processes, replaying commands, or freeing route-bound ambiguous work.

## 8. Exact next frontier

The current source-level coordinator is substantially built and green. The next highest-value work is **operational qualification and real host execution**, not another architecture rewrite.

Fresh chat should:

1. Fresh-read PR #1 head and exact-head CI.
2. Compare the live head against this checkpoint and inspect unexpected movement before mutation; never force.
3. Inspect outstanding delegated/mutation ownership before writing shared subjects.
4. Read current `PORTAL.md` and `docs/operations/PORTAL_HOST_BRIDGE_RUNBOOK_V1.md`.
5. Refresh execution adapters actually live in the new ChatGPT runtime.
6. Refresh live Project Runner task/currentness and host occupancy; classify stale/orphaned vs truly active work.
7. Choose a **bounded non-protected test portfolio** and advertise exact target routes.
8. Register concrete host drivers only for routes the current host can actually drive.
9. Run one real closed-loop qualification:
   - refresh currentness;
   - admit;
   - bind exact route;
   - queue;
   - durable take/attempt marker;
   - execute a reversible/non-protected effect;
   - owning-substrate verification;
   - reconcile;
   - prove capacity release;
   - prove automatic refill or verified idle.
10. Exercise crash/fresh-chat recovery of an `OUTCOME_UNKNOWN` attempted effect and prove that it remains active + route-pinned until reconciled.
11. If bounded qualification is green, widen to more portfolio lanes while preserving collision, occupancy, route-currentness, and authority ceilings.
12. Fix any discovered source defects using true red -> green -> regression.
13. Persist another checkpoint before the next chat/runtime boundary.

Do not stop after a status recap. Produce useful work after orientation.

## 9. Authority

Current task allows:

- working-branch source changes;
- working-branch tests;
- working-branch documentation;
- durable continuation/checkpoint writes;
- bounded reversible/non-protected operational qualification.

Patrick has **not** authorized:

- merge of PR #1;
- direct main mutation;
- deployment;
- native Project install/replacement;
- production-provider mutation;
- credentials/permissions changes;
- paid spend;
- destructive cleanup;
- protected-effect promotion;
- canonical-memory writes;
- private publication.

## 10. Evidence discipline

For behavior changes:

- freeze exact subject/head first;
- create the smallest public-seam regression;
- observe the intended red;
- implement the minimum correct fix;
- rerun focused tests;
- run full repository regression/CI when the central path changes;
- verify the exact pushed head;
- never weaken or delete the relevant assertion to manufacture green.

For mutations/effects:

- inspect target/currentness first;
- use CAS/non-force where possible;
- if the target moves unexpectedly, reread/reconcile;
- after ambiguous non-idempotent effects, verify/reuse or classify `OUTCOME_UNKNOWN`; never blind retry.

## 11. Continuation chain

Predecessor durable continuation:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1351-0400`

This checkpoint supersedes it for chat restoration but does not erase its provenance.

## 12. Restore instruction

Exact new-chat restore grammar:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1439-0400`

Expected lookup:

- repo: `thebrazenbeard/portal`
- branch: `work/portal-coordinator-v1`
- files:
  - `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1439-0400.md`
  - `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1439-0400.json`

On restore: treat the checkpoint as durable evidence, refresh mutable state, then resume the highest-value non-colliding runnable frontier.
