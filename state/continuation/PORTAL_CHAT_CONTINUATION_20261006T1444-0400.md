# P.O.R.T.A.L. Chat Continuation — 2026-10-06 14:44 ET

**Schema:** `PORTAL_CHAT_CONTINUATION_V2`  
**Logical ID:** `PORTAL_CHAT_CONTINUATION_20261006T1444-0400`  
**Repository:** `thebrazenbeard/portal`  
**Working branch:** `work/portal-coordinator-v1`  
**Draft PR:** `#1 — P.O.R.T.A.L. portfolio coordinator v1`  
**Base:** `main@fa125a74bd42bc2bdabf3f8f9ef677b225db6f76`  
**Branch head immediately before this checkpoint:** `17afbcf3b7dd7882be8b47adf73e2c822145b30e`  
**Implementation subject beneath continuation-only commits:** `618a5f63211b13d390b74d522734fca9bf743bbc`  
**Implementation-head message:** `docs: document atomic project route advertisement`  
**Checkpoint reason:** Patrick explicitly said this chat is full and ordered the work saved to the repository with a durable continuation point and a command for a new chat.

## 1. Standing task

Patrick's standing instruction remains:

> Resume and build the portal repo and populate it until it works like we want it to.

P.O.R.T.A.L. is intended to be one durable, client-independent whole-portfolio coordinator that:

- refreshes repository/project/currentness state;
- computes maximal safe parallel work;
- accounts for already-active, held, terminal, and ambiguous subjects;
- accounts for live occupied capacity rather than only declared capacity;
- selects exact qualified routes by target, currentness, attachment, capability, authority, and host policy;
- durably binds an exact route before an effect attempt;
- dispatches through existing execution substrates rather than replacing them;
- reconciles through the owning execution substrate;
- keeps ambiguous attempted effects active and route-pinned;
- releases capacity only after verified completion/held evidence;
- automatically refills freed capacity;
- distinguishes stale/orphaned records from genuinely active work;
- survives chat/process loss through durable state;
- exposes clean top-level `run / continue / hold / complete / status / stop`;
- works from ordinary ChatGPT web/HTML, with Desktop/workstation routes optional rather than required.

Do **not** restart the architecture.

## 2. Current exact source state

Fresh PR #1 read immediately before this checkpoint:

- state: **OPEN**
- draft: **true**
- branch: `work/portal-coordinator-v1`
- head: `17afbcf3b7dd7882be8b47adf73e2c822145b30e`
- base: `main@fa125a74bd42bc2bdabf3f8f9ef677b225db6f76`

Current head message:

`state: bind Portal chat continuation 20261006T1439`

Exact-head CI at `17afbcf3b7dd7882be8b47adf73e2c822145b30e`:

- GitHub Actions `test`: **SUCCESS**
- pytest: **672 passed in 39.69s**
- `task-monitor-windows`: **SUCCESS**
- Dependency Review: **SUCCESS**

This is source/test evidence only. It does **not** prove install, resident host runtime, current route, runtime consumption, external effect closure, or whole-portfolio operational qualification.

## 3. Implementation subject

The current implementation subject remains:

`618a5f63211b13d390b74d522734fca9bf743bbc`

Message:

`docs: document atomic project route advertisement`

The commits after that implementation subject and through current head are continuation/checkpoint state. The immediate predecessor checkpoint is:

`PORTAL_CHAT_CONTINUATION_20261006T1439-0400`

Restore grammar:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1439-0400`

This new 14:44 checkpoint supersedes 14:39 for chat restoration while preserving it as provenance.

## 4. Source-level capabilities already present

Current branch contains the main source-level coordinator architecture, including:

- durable command-session projection;
- `run / continue / hold / complete / status / stop`;
- resident bounded reconcile/refill;
- active/held/terminal durable projection;
- verification-gated completion and capacity release;
- active-subject collision/budget propagation;
- live occupied-node accounting;
- capability/currentness/authority-based route resolution;
- composite execution routing;
- durable bind-before-dispatch evidence;
- adapter-owned reconciliation;
- no silent route substitution after an effect attempt;
- `OUTCOME_UNKNOWN` remains active and route-pinned;
- external host bridge with expiring route, occupancy, and frontier currentness;
- atomic host dispatch take/attempt boundary;
- bounded host pump;
- host driver registry;
- command-backed host driver support;
- semantic frontier currentness;
- Project Runner task currentness;
- live portfolio membership/currentness;
- diagnostics;
- exact project-level route advertisement;
- source-level closed-loop acceptance coverage.

Major files include:

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

## 5. Core invariants

Preserve these:

1. **P.O.R.T.A.L. owns coordination state; execution substrates own execution/effect state.**
2. Active subjects consume collision and scheduling budgets.
3. Worker/process success is not verified completion.
4. Capacity release requires owning-substrate verification.
5. Bind exact route durably before effect attempt.
6. After ambiguous attempt, keep subject route-pinned until reconciliation.
7. No silent route substitution after the attempt boundary.
8. Availability != attachment != currentness != technical capability != effect authority.
9. Declared max capacity != live free capacity.
10. `hold` is durable no-refill intent, not cancellation authority.
11. `stop` blocks new admission/refill while preserving active/unresolved work.
12. Source/test PASS != install != current route != runtime consumption != effect closure.
13. Explicit route selection fails closed if that exact route is not qualified.
14. Plugin/workstation discovery never manufactures authority.
15. Stale/orphaned task records do not consume live capacity once authoritative reconciliation proves the registered process identity is gone/reused; ambiguous identities stay unresolved rather than being guessed away.

## 6. Correct adapter model

Do not restore the obsolete rule “WorkLaptop routes only Executor.”

Execution routing is target-bound and capability-based. Candidate routes may include, only when live/current/attached/capable/authorized for the exact target/effect:

- repo-native GitHub/plugin routes;
- WorkBridge Commander;
- WorkBridge Relay when it actually exposes required capability;
- Executor;
- Lappy Desktop Commander V2;
- other service-native/plugin adapters.

Core P.O.R.T.A.L. must not understand every provider directly. Adapter-specific details stay behind capability/evidence/reconciliation contracts.

One subject may use different substrates across distinct phases, but after an effect attempt is route-bound, ambiguous outcome must be reconciled before substitution.

## 7. Operational host sequence

Current source/runbook sequence:

`REFRESH -> ADVERTISE -> INSPECT -> ADMIT -> BIND -> QUEUE -> TAKE/ATTEMPT-MARK -> EFFECT -> VERIFY -> RECONCILE -> REFILL`

Queued work alone never authorizes an effect. The durable attempt marker must precede the effect call.

## 8. Stale-work problem Patrick explicitly wants solved

Patrick observed a large amount of stale-looking work and specifically asked P.O.R.T.A.L. to build the refill behavior because tasks appeared stalled while Tattler kept moving.

Historical development evidence showed many Project Runner registrations classifiable as stale/orphaned while relatively few tasks were genuinely running.

Do not treat that historical observation as current fact. On restore, fresh-read:

- Project Runner task currentness;
- active/history registries;
- host process identity/currentness;
- live route advertisements;
- live occupancy.

Do not solve stale work by blind kills, blind replay, freeing route-bound ambiguous effects, or silently moving attempted work to another adapter.

## 9. Exact next frontier

The source-level coordinator is substantially built and green. The highest-value next work is **operational qualification against real current adapters and stale/live task state**, not another architecture rewrite.

Fresh chat should:

1. Fresh-read Draft PR #1 head and exact-head CI.
2. Compare live head with this checkpoint; inspect unexpected movement before writing; never force.
3. Inspect current mutation/delegation ownership before shared writes.
4. Read current `PORTAL.md` and `docs/operations/PORTAL_HOST_BRIDGE_RUNBOOK_V1.md`.
5. Refresh the adapters actually live in the new ChatGPT runtime.
6. Refresh Project Runner task/currentness, process identity, route advertisements, and occupancy.
7. Classify current stale/orphaned vs active/identity-unverified/ambiguous work without destructive cleanup.
8. Choose a bounded non-protected test portfolio with at least one genuinely free route/capacity slot.
9. Register/advertise only exact routes the current host can actually drive.
10. Run one real closed-loop qualification:
   - refresh;
   - admit;
   - bind exact route;
   - queue;
   - durable take/attempt marker;
   - execute one reversible/non-protected effect;
   - owning-substrate verify;
   - reconcile;
   - prove capacity release;
   - prove automatic refill or verified idle.
11. Exercise recovery of one `OUTCOME_UNKNOWN` attempted effect across a fresh process/chat and prove it remains active + route-pinned until owning-route reconciliation.
12. Exercise stale task reconciliation so dead/PID-reused registrations stop polluting occupancy without killing or replaying live/ambiguous work.
13. If bounded qualification is green, widen to additional portfolio lanes while preserving collision, occupancy, route-currentness, and authority ceilings.
14. Fix any discovered source defect with true red -> green -> full regression.
15. Persist another continuation before the next chat/runtime boundary.

Do not stop after a status recap. Do useful work after orientation.

## 10. Authority

Current standing task permits:

- working-branch source changes;
- working-branch tests;
- working-branch documentation;
- durable continuation/checkpoint writes;
- bounded reversible/non-protected operational qualification.

Not authorized without new exact authority:

- merge PR #1;
- direct main mutation;
- deployment;
- native Project install/replacement;
- production-provider mutation;
- credential/permission changes;
- paid spend;
- destructive cleanup;
- protected-effect promotion;
- canonical-memory writes;
- private publication.

## 11. Evidence discipline

For behavior changes:

- freeze exact subject/head;
- create smallest public-seam regression;
- observe intended red;
- implement minimum fix;
- rerun focused tests;
- run full regression/CI for central-path changes;
- verify exact pushed head;
- never weaken/delete the assertion to manufacture green.

For mutations/effects:

- inspect exact target/currentness first;
- prefer CAS/non-force writes;
- unexpected movement => reread/reconcile;
- ambiguous non-idempotent effect => verify/reuse or classify `OUTCOME_UNKNOWN`;
- never blind retry.

## 12. Continuation chain

Immediate predecessor:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1439-0400`

Earlier predecessor:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1351-0400`

This checkpoint supersedes both for restoration, without erasing their provenance.

## 13. Restore instruction

Exact new-chat restore grammar:

`PORTAL::RESTORE::PORTAL_CHAT_CONTINUATION_20261006T1444-0400`

Expected lookup:

- repo: `thebrazenbeard/portal`
- branch: `work/portal-coordinator-v1`
- files:
  - `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1444-0400.md`
  - `state/continuation/PORTAL_CHAT_CONTINUATION_20261006T1444-0400.json`

On restore: treat this checkpoint as durable evidence, refresh mutable Git/CI/route/task state, reconcile unexpected movement and ambiguous effects, then resume the highest-value non-colliding runnable frontier.
