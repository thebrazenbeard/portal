# Project Runner Operating Contract

This document states Project Runner operational invariants. It does not grant authority over downstream projects.

## Public-safe repository

Everything committed here must be safe for public disclosure. Do not commit credentials, private source payloads, private relational/autobiographical material, confidential mechanisms, or private project contents.

Portfolio relevance does not imply publication authority. The committed project registry is a public-safe seed, not the complete private portfolio. Additional private project identifiers belong in a complete external registry selected explicitly with an absolute `PROJECT_RUNNER_PROJECT_REGISTRY` path and an exact `PROJECT_RUNNER_PROJECT_REGISTRY_SHA256` byte binding. The runtime treats that file as a replacement registry rather than silently merging it into public source.

External records must explicitly declare scheduling posture. `HELD` records are observable portfolio state but are not schedulable and contribute no runnable capabilities. Detailed CLI reports that would expose project IDs, repositories, dependency IDs, subjects, or collision keys are disabled while an external registry is selected.

Private collision domains must be derived with separate runtime secret material, not the registry digest or another public/currentness token. `PROJECT_RUNNER_PRIVATE_COLLISION_KEY` remains outside public source/evidence and must be stable across any restart that is expected to preserve private semantic/collision identity.

Blocked private work remains operator-visible through `frontier-summary` aggregate status counts. The private-safe surface must not emit identifiers, subjects, dependency/collision metadata, reasons, or private input paths; malformed/private inputs fail with a generic structural error rather than falling back or echoing sensitive detail.

The three private project identifiers already present on canonical M5 `main` are legacy public baseline metadata. Their prior disclosure does not authorize adding more private identifiers.

## Runtime and worker reconstruction

Project Runner requires no permanent Project Runner chat. ChatGPT, Work, API, CLI, subagent, and model sessions are execution terminals only; current portfolio state and worker scope must be reconstructed from durable GitHub/Bus/registry/evidence state. See `docs/PROJECT_RUNNER_WORKER_RECONSTRUCTION_V1.md`.

Custom GPT IDs and other endpoint locators are locators only. A route marked `UNVERIFIED` is not an executable path, authority grant, or current worker state. Conversation URLs must never be the sole recovery locator.

## Evidence and authority

Observations, registry entries, workflow results, reviews, frontiers, priority decisions, work units, backend results, leases, and receipts are evidence or coordination state. None grants authority by itself.

## M5 GitHub execution boundary

Project Runner now has a real GitHub REST backend, but technical execution capability and target authority are distinct.

A GitHub operation proceeds only when:

1. the backend route advertises the required technical capability;
2. an explicit target grant permits the exact repository and operation;
3. the requested ref/path is inside that grant;
4. mutable predecessor state matches the request's expected head/blob where required;
5. the effect is read back after mutation;
6. later completion verification still rechecks exact currentness and independent evidence.

Possessing a token with broad GitHub permissions does not satisfy target authority.

### Persistent lineage budget

M5 includes a SQLite lineage budget ledger with generation compare-and-swap. A workflow/process may reserve remaining lineage budget; a stale generation cannot overwrite a newer reservation. Restarting execution therefore cannot silently restore consumed child/active/retry/backend-job quota.

### Persistent leases and fencing

M5 includes a SQLite lease store. Claim/reclaim state survives process restarts. Expired work can be reclaimed with a strictly higher fencing token. Older holders cannot complete or release the reclaimed work.

### Durable dispatch admission

M6 reserves execution state before backend work begins. One SQLite transaction re-reads the exact durable WorkUnit and budget generation, verifies the WorkUnit integrity/capability ceiling and dispatch-admissible lifecycle state, claims or reclaims the lease with a monotonic fencing token, consumes active/backend-job quota (and retry quota when redispatching retryable/unknown work), advances the budget generation, and moves the WorkUnit to `CLAIMED`.

The backend is invoked only after that transaction commits. A crash after durable admission therefore cannot execute work while leaving durable quota unconsumed or the lease/WorkUnit ownership forgotten. Stale budget/work generations, active-lease collisions, exhausted quota, completed work, or integrity/currentness mismatch roll the transaction back without partial reservation.

### Persistent recursive work lineage

M6 persists each recursive work subject under `(lineage_id, semantic work fingerprint)` together with its exact immutable work payload, parent semantic fingerprint, exact ancestry set, budget scope, lifecycle status, and generation.

Immutable recursive state is digest-verified on read. A child may be persisted only after its parent is already durable, its ancestry must equal the durable parent ancestry plus the child's own semantic fingerprint, and its budget scope must be `work:<fingerprint>`. Root work uses the `root` scope.

Lifecycle updates use generation compare-and-swap and an explicit transition map. Entering any lease-bound nonterminal state—`CLAIMED`, `RUNNING`, `VERIFYING`, `FAILED_RETRYABLE`, or `OUTCOME_UNKNOWN`—requires the exact current lease holder/fencing token and an unexpired lease in the same SQLite transaction. Generation knowledge alone cannot make a caller the execution owner. Retryable and unknown outcomes remain recoverable: when the owning lease expires, a valid reclaimed higher fence may resume them through the allowed lifecycle. Lease ownership still does not permit lifecycle skips or backward transitions.

Durable terminalization is a separate atomic operation. The durable M6 path asks verification to propose an outcome without mutating the lease, then `finalize_terminal_status` rechecks the exact active fence and commits the lease-finalization effect plus WorkUnit terminal status/generation in one SQLite transaction. `COMPLETE` marks that exact lease row completed; `FAILED_DETERMINISTIC` and `SUPERSEDED` release that exact fence. Direct terminal status CAS is rejected. This removes the prior crash window where a lease could be durably completed while the WorkUnit remained stranded in `VERIFYING`. Reclaimed/stale fences cannot terminalize work. `COMPLETE`, `FAILED_DETERMINISTIC`, and `SUPERSEDED` remain immutable terminal states; active work cannot reset to `PENDING`; same-status updates are idempotent.

Recursive child admission is transactional across all durable state it creates or consumes. Every durable work record integrity-binds its effective capability ceiling. The admission transaction re-reads the exact durable parent work, verifies its immutable digest and nonterminal status, re-reads that durable parent capability ceiling and the exact parent budget generation, and re-runs `admit_child_work` using the durable parent ceiling plus the current target capability ceiling. The caller cannot supply or widen the parent ceiling at commit time. The caller-supplied admission must exactly equal that recomputed result. Only then may the same transaction decrement the parent budget and insert the child budget plus child work/ancestry/capability record. Any collision, stale generation, forged depth/capability, missing/tampered parent, or other failure rolls the whole transaction back.

A pre-capability-ceiling recursive record is migration-eligible only if its legacy immutable digest verifies first. Its migrated effective ceiling is the conservative set of capabilities that the frozen work record itself required; migration never infers or manufactures a broader historical ceiling.

Ordinary initial-state APIs accept root scope only. Child budget/work creation outside the atomic recursive-admission path is rejected.

### GitHub operations

The reference backend supports:

- exact ref read;
- create branch from an exact expected source head;
- create/update UTF-8 file under an authorized ref/path scope with expected-state checks and post-write readback.

Existing-file updates require the expected blob SHA; blind stale overwrite is rejected.

### CI proof

The repository CI uses the actual GitHub REST transport in read-only mode against the exact push branch and requires the observed head to equal the workflow subject SHA.

CI retains `contents: read`. The live smoke test is connectivity/currentness evidence, not downstream mutation authority.

## Preserved M4 rules

- child capabilities are intersections, never expansions;
- recursive work consumes inherited budget;
- semantic work deduplicates before claim;
- collision domains serialize conflicting mutable targets only;
- recursion/cycles terminate fail-closed;
- backend/worker success is not completion;
- stale output becomes SUPERSEDED rather than COMPLETE;
- unresolved currentness or fencing becomes bounded OUTCOME_UNKNOWN.

## M6 operator route

The ordinary CLI exposes a bounded real operator route through run-inspection. It accepts only an already-derived READY/INSPECT frontier, binds an exact target repository/ref/head and the current project-registry digest into durable work identity, persists budget/work/lease/journal state, uses the real GitHub read backend, independently rechecks exact subject currentness, and atomically finalizes terminal verification evidence.

operator-status reports unresolved durable recovery classes without re-executing backend work. An ADMITTED attempt with no recorded result remains an ambiguous-effect state and is never blindly retried.

The default state database is local SQLite. Its durability is scoped to the filesystem retaining that database; ephemeral CI storage is not cross-run persistence.

## M6 durable portfolio currentness

Portfolio currentness is a separate read-only durability boundary from execution admission.

A portfolio cycle must use one exact project-registry snapshot, one exact dependency-registry snapshot, and one exact worker-registry snapshot. Dependency edges may create READ_REF target grants only after the provider and consumer exist in that project snapshot, the selector repository is registered to the declared provider, and the selector has an exact ref.

The first successful cycle for a dependency-registry digest establishes the observation baseline. Project- and worker-registry changes on that same dependency topology do not discard observation history or unresolved work: current exact-subject frontiers are recovered from durable topology history and re-evaluated under the current scheduling/authority state. A dependency-topology digest change still establishes a new observation baseline.

All required ref reads must succeed before a snapshot can advance. Snapshot persistence and scheduler decisions are one SQLite transaction. The transaction rechecks the exact latest compatible predecessor under `BEGIN IMMEDIATE`; a stale concurrent collector must fail rather than overwrite or double-schedule from an obsolete predecessor.

READY frontiers are persisted as `QUEUED`; non-ready frontiers are persisted as `BLOCKED`. A frontier is not READY for execution unless the current project snapshot contains one explicit execution target for its work type. Repository membership alone does not authorize a ref, and `main` is never inferred.

## M6 fenced queue consumption

Queue consumption is separately fenced from operator execution. A consumer may claim only a READY supported work item from the latest snapshot whose project/dependency/worker digests equal the current configuration. Claims use monotonic fencing tokens and respect live collision domains across snapshots.

The exact declared target repository/ref is stored with the queue claim. Its resolved exact head is persisted before operator execution and remains stable across reclaim; a reclaimed attempt cannot silently select a newer target head.

The queue derives a deterministic operator lineage from durable snapshot/frontier identity. Before execution it reconstructs the exact expected operator work fingerprint. Existing terminal operator state is reconciliation evidence and must be consumed without backend re-execution. Existing nonterminal state is ambiguous and must become queue `OUTCOME_UNKNOWN`; it does not authorize blind replay.

Pending queue work is semantic, not tied to the newest observation row. Unclaimed or retryable work remains recoverable across compatible snapshots while its full exact provider subject is still current. A newer subject makes the older frontier non-claimable. Historical terminal state acts as a tombstone for that semantic fingerprint, preventing resurrection from an older row. Queue state remains coordination/evidence, not downstream mutation authority.

## M6 queue reconciliation and read-only worker routing

A project may bind an execution target to a worker ID and exact invocation route. That binding becomes runnable only when the exact worker-registry snapshot contains one matching worker that is `EXECUTABLE`, the target route is `VERIFIED`, and the same route has an explicit `READ_ONLY` effect contract. Worker registration, locator presence, connected state, or a verified different route does not qualify the target route.

Qualified non-INSPECT work is handed off through a durable worker-route outbox. Queue transition to `ROUTED` and outbox insertion are one transaction. The outbox payload binds worker-registry digest, queue fencing token, worker/route identity, replay policy, exact target head, and exact frontier payload. Creating the envelope is routing evidence, not proof that an external worker ran. Delivery is separately fenced: the exact current worker registry must still show the worker EXECUTABLE, the exact route VERIFIED, and the route READ_ONLY before an envelope can be pulled. The provider exact subject is rechecked against the latest compatible portfolio currentness before delivery; stale packets are superseded rather than handed to a worker. Worker packets are local sensitive artifacts: CLI delivery writes them `0600` and refuses external/private portfolio packets inside the public checkout. A worker receipt is accepted only from the exact route identity, holder, and delivery fence.

`ROUTED` and `OUTCOME_UNKNOWN` reserve their collision domains until explicit reconciliation. SAFE delivery routes may reclaim an expired delivery fence. RECONCILE_REQUIRED routes turn an expired claim into ambiguous delivery state and require explicit reconciliation before another attempt; NEVER routes cannot be retry-released. Reconciliation requires the exact queue subject/fence plus an evidence SHA-256 and reconciler identity. Routed receipt classes must be compatible with the requested resolution. `CONFIRM_COMPLETE` additionally re-reads the provider ref and exact bound target ref live and rejects stale work even when the worker receipt says SUCCEEDED. A terminal confirmation updates queue state and any matching routed outbox in the same transaction. Any permitted retry release increments attempt generation and derives a new deterministic lineage; the old execution identity is never reused as fresh work.

The evidence digest binds the operator's reconciliation record; it is not independently self-authenticating proof of the external event.

## Multi-lane scheduling and local task identity

Project Runner may classify advancement work into explicit scheduling lanes. Lane membership is routing/capacity metadata only; it is not repository authority, execution authority, protected-effect authority, or a new semantic work identity. When no explicit lane is declared, the lead identity is the effective lane for backward-compatible scheduling.

Global collision keys remain global across lanes. The planner may enforce a separate per-lane capacity in addition to global, per-identity, and per-family ceilings. A saturated lane does not stall unrelated eligible lanes, but a different lane never bypasses a collision or authority gate.

Lane assignments are bound by the admission-plan digest but are intentionally excluded from the selected work payload consumed by the operator bridge. Moving the same work between lanes therefore changes the scheduling plan without silently redefining the durable downstream work subject.

The local Windows task monitor may filter and summarize tasks by lane. When a registered task includes process creation time, liveness must bind both PID and creation time so PID reuse cannot impersonate the registered supervisor. An unverifiable process identity remains nonterminal and must not be reconciled as dead merely because creation-time readback failed.

See `docs/MULTI_LANE_TASK_HANDLING_V1.md`.

## Current effect ceiling

M6 does not grant standing mutation authority over another repository, deploy production systems, create credentials, invoke Custom GPTs, or infer authority from connector/token permission. The first operator route is intentionally read-only.

The twelve Custom GPT records remain registrations with UNVERIFIED routes until an end-to-end executable path is independently demonstrated. They are not promoted by the reference worker. Project Runner separately owns one GITHUB_ACTION reference read worker whose `RUNNER_ACTION_PULL` route is VERIFIED only at the READ_ONLY/SAFE ceiling and whose exact-head CI proof must live-read the configured GitHub ref and persist a fenced receipt.
