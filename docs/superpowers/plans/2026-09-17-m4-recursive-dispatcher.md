# Project Runner M4 — Recursive Dispatcher Implementation Plan

Date: 2026-09-17
Branch: `vera/m4-recursive-dispatcher`
Base: `7b94613afb28d167076de64a4e5cdabde23cc479`

## Goal

Turn M3 frontiers into bounded executable work units without allowing recursion, concurrency, leases, budgets, or worker delegation to create new authority or accept stale results.

M4 remains a local/mock execution milestone. It does not add downstream credentials, production mutation, GitHub Actions dispatch, Custom GPT execution, deployment authority, or provider mutation.

## Hard invariants

- Child capability = intersection of parent capability and target-declared capability. Delegation can narrow, never expand.
- Child budget comes from an existing lineage budget. Recursive spawning cannot create budget.
- Work identity is semantic and independent of scheduling order or provenance-path count.
- Equivalent claims are atomic at the lease store boundary, not merely deduplicated in memory.
- Lease recovery uses expiry plus monotonic fencing tokens so a stale worker cannot commit after replacement.
- Recursive lineage detects ancestry cycles and configured depth exhaustion.
- Work is accepted only after exact-subject currentness is rechecked after execution.
- Worker completion claims are evidence; verification decides whether a frontier can become COMPLETE.
- Collision domains describe mutable targets and serialize only conflicting work.
- M4 mock/local execution has no downstream side effects.

## Task 1 — Work-unit model and stable fingerprint

Add:
- `WorkUnitStatus`
- `WorkUnit`
- immutable parent/root lineage identifiers
- exact input subjects
- requested operation
- required capabilities
- collision keys
- recursion depth
- budget allocation
- expected outputs/completion criteria
- deterministic semantic fingerprint

Tests:
- fingerprint stable across incidental ordering
- different exact subject/operation produces different fingerprint
- work id is not part of semantic identity
- invalid negative depth/budget rejected

## Task 2 — Capability narrowing

Add a pure capability-inheritance function.

Tests:
- child gets intersection only
- absent target capability cannot be inherited
- parent cannot delegate a capability it lacks
- empty intersection is represented explicitly and blocks dispatch

## Task 3 — Lineage budgets

Add immutable/persistable budget envelopes covering:
- max recursion depth
- remaining child units
- remaining active/run units
- remaining retries by class
- optional backend-job allowance

Tests:
- child allocation subtracts from parent envelope
- recursive children cannot reset counters
- over-allocation fails closed
- depth exhaustion terminates decomposition

## Task 4 — Atomic leases and fencing

Define a lease-store interface and an in-memory reference implementation with:
- atomic claim-if-unclaimed-or-expired
- lease id / work fingerprint
- holder
- expiry
- heartbeat
- monotonic fencing token
- reclaim after expiry
- release/complete transitions

Tests:
- two simultaneous claim attempts yield one owner
- non-expired lease cannot be stolen
- expired lease can be reclaimed with higher fence
- stale fence cannot complete/release reclaimed work
- heartbeat extends only the current fenced lease

## Task 5 — Recursive decomposition guards

Add pure decomposition validation:
- semantic child distinctness
- inherited/narrowed capability
- inherited budget
- ancestry fingerprint set
- cycle rejection
- depth termination
- collision metadata preservation

Tests:
- self-cycle rejected
- A→B→A lineage rejected
- valid independent children admitted
- child cannot expand authority/budget

## Task 6 — Mock backend and dispatcher

Add:
- backend protocol
- deterministic mock backend
- dispatcher that consumes ranked READY frontiers
- collision-aware claim selection
- lease acquisition
- bounded execution
- result collection

No actual downstream mutation is allowed in M4.

Tests:
- blocked frontiers never dispatch
- unrelated READY work can proceed in parallel planning
- colliding work admits only one active representative
- duplicate semantic work claims once

## Task 7 — Post-work exact-currentness verification

Before accepting success:
- re-observe/recheck the declared exact subject through an injected verifier
- if unchanged and completion evidence passes -> VERIFYING/COMPLETE path
- if moved -> SUPERSEDED / new frontier signal
- if currentness cannot be established -> OUTCOME_UNKNOWN or bounded verification failure

Tests:
- stale output never becomes COMPLETE
- unchanged exact subject can complete
- worker self-claim alone is insufficient

## Task 8 — End-to-end M4 report

Add a deterministic CLI/report fixture that performs:

M2 observations -> invalidations -> M3 frontiers -> dedup/rank/collision partition -> M4 work-unit creation -> mock dispatch -> verification result.

Document:
- what M4 does
- what remains intentionally absent until M5
- lease/fencing and budget semantics
- exact-subject post-check requirement

## Completion gate

M4 is complete only when:
1. the full repository test suite passes on the exact feature head;
2. registry validation passes;
3. branch is still based on the expected canonical M3 lineage or divergence is reconciled;
4. no test or implementation path performs downstream protected effects;
5. README / operating contract accurately state the M4 ceiling.
