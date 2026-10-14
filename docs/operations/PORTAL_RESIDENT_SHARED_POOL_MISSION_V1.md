# Portal Resident Shared-Pool and Portfolio Mission — V1

Status: SOURCE CANDIDATE / TEST GATED. Not installed, selected, activated, or full-portfolio qualified. Source base: `portal/main@c5c7bf9ee4100658f85ffa000175fb2361831f34`.

## User outcome and boundary

The desired interaction is one durable instruction: **Make the portfolio work as a coherent ecosystem on Lappy.** This requires discovery, currentness checks, classification of which repositories actually need to integrate, actionable gap selection, bounded delegation, repairs, verification, installation, runtime-consumption tests, and feedback without repeated per-repository commands.

Portal is the coordination plane and Project Runner supplies admission and per-subject work contracts. The request is NOT authority to make all repositories interdependent or to publish private repositories. Independent products and donor code must remain independent unless a current verified interface actually requires them.

The implementation in this candidate supplies ONLY two narrow missing mechanisms, not the entire program:

1. `scripts/portal_resident_reentry.py` polls a previously created durable Portal command session and executes `continue --resume` using its stored scope, preserving decisions when a conversational turn ends. It refuses an absent state database, unknown control state, STOPPED session, ambiguous result, and command failure. It logs each completed poll and keeps at most 100 summaries in memory. It is *not* a boot service; startup/service identity, single leader, operational restart, health checks and install validation still need qualification. Explicit `--max-polls 0` is unbounded while the operator's process exists.
2. `portal host pump --shared-pool` and session `--host-shared-pool` consume suitable **unattempted queued** jobs from the shared SQLite queue. Instead of reading a snapshot and racing individual mark-attempt operations, each host calls the existing `take_pending_dispatch` atomic transaction, filtered by its supported adapter IDs and live exact-route qualification. CLI defaults to one bounded dispatch per shared-pool pass unless a positive `--max-dispatches` is given. Recorded `ATTEMPTED` work is never stolen or automatically retried.

## Why this is not a finger-tree rewrite

A finger tree is a measured persistent sequence suited to split/concatenate and certain indexed summaries. The current shared backlog is a **durable SQL ownership protocol** rather than a local Python deque. Replacing it with an in-memory finger tree would discard transactional exclusivity and complicate restart/reconciliation. The `deque(maxlen=100)` in the resident wrapper is just bounded observation retention, not the scheduling queue.

The useful combination is **work sharing + controlled measured scheduling**, not necessarily a finger-tree implementation. A later candidate can benchmark chunking contiguous *queued and unclaimed* job families using estimated cost, dependencies, available workers, and cache locality. Do not introduce a new data structure unless that benchmark beats SQLite indexed selection without weakening ownership and crash safety.

## Qualification invariants

- Only the already admitted subject, its exact repository/ref, route, capability set, effect ceiling and source-currentness evidence can execute. Shared-pool claiming must not mint authority or bypass host probes/grants.
- The durable attempt marker precedes driver execution. Ambiguous outcomes remain ATTEMPTED and block automatic replay. The worker must explicitly reconcile with effect evidence.
- Concurrent workers cannot both cross the attempt boundary for the same dispatch. Cross-adapter mismatch, stale route, disabled node or unsupported effects must remain QUEUED/held as appropriate.
- A STOPPED session is never restarted by the resident wrapper. Re-entry must never invent a portfolio mission, route, grant or worker.
- A green source suite and a successful local lab are not an installed resident, a deployed host, or verified portfolio advancement.

## Program to achieve the single-request outcome (still missing)

1. **Discover and bind scope:** authenticated owner inventory (including private count if authorized), canonical/active branches, delegation ownership, repository role classification. Do not claim public-only discovery is complete.
2. **Build a relation graph:** separate internal runtime components, cross-repository APIs, independently deployed products, infrastructure and donor/provenance repos; identify actual compatibility failures with testable propositions. Preserve owners and isolated worktrees.
3. **Select and schedule bounded fixes:** rank genuine blockers by impact and verified dependency path, synthesize minimally scoped work packets, honor priority/skills, avoid simultaneous mutation of the same exact subject, and abstain when evidence is insufficient.
4. **Execute and verify:** independent source tests, integration contracts, crash/restart and adverse concurrency tests; retain negative evidence; exact commit heads, original PR ownership and review statuses.
5. **Deploy where authorized and qualified:** package/install/current-route/live-consumption/effect gates are separate. Use canaries, rollback and readback; do not claim working because a PR merged or a process merely started.
6. **Continue without spoon feeding:** persistent host process with watchdog and durable task/session ledger, anomaly visibility, admission of new gaps only after fresh evidence, cost/parallelism budgets and a termination criterion for each mission.

Acceptance requires a live, first-to-last demonstration on a representative portfolio slice (not just four synthetic repositories): current discovery -> selected real issue -> bounded repair -> tests/PR -> approved integration -> installed on Lappy -> functional behavior probe -> restart and continued progress. Only then consider increased coverage. This artifact supplies none of those end-to-end production proofs.

## Hostile review

> Objection: `take_pending_dispatch` already exists, so this shared-pool method mostly exposes an existing capability; the real source of constant prompting may be the missing resident, provider execution and autonomous gap selection rather than queue contention.

**Accepted.** This patch reduces specific queuing friction and strengthens proof of concurrent use; it is not a solution to mission planning. Do not turn its tests into claims of general autonomous improvement.

> Objection: Work sharing without moving an exact bound route is not true task migration or generalized work stealing.

**Accepted.** The truthful claim is *cooperative consumption of qualified unclaimed work*. Route changes after admission require explicit reassignment and all associated fencing and authority proofs; do not add opportunistic theft of ATTEMPTED tasks.

> Objection: A looping subprocess wrapper could spin indefinitely when no work is available or restart after a process crash.

**Partially accepted.** It sleeps and refuses STOPPED/ambiguous states, but there is no installed service manager, fault-tolerant retry policy or automatic new mission selector. Do not activate as a resident production service without those additional checks.
