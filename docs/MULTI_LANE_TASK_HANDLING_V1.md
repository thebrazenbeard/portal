# Multi-Lane Task Handling V1

Project Runner lanes are **scheduling domains**, not authority domains and not new work identities.

## Why lanes exist

Project Runner already has durable semantic work identity, collision domains, queue claims, leases, fencing tokens, retries/reconciliation, worker routing, and effect ceilings. The missing layer was a way to express that several independent streams may make progress concurrently without letting one busy stream consume every available slot.

The design therefore adds lane capacity without adding a second scheduler.

An advancement item has an effective lane:

```
effective_lane = explicit lane if present else lead_identity
```

The fallback preserves existing waves unchanged. Explicit lane labels let one identity own several independent streams or several identities share one bounded progress domain.

## Admission order and invariants

Wave admission remains deterministic and fail-closed.

1. Item must be QUEUED.
2. Inert actions remain deferred.
3. Effect ceiling must remain SOURCE_ONLY or NO_EFFECT.
4. Global collision keys are checked before capacity.
5. Global parallel capacity is enforced.
6. Optional per-lane capacity is enforced.
7. Per-identity capacity is enforced.
8. Per-family capacity is enforced.
9. Only then is the item selected and its collision keys reserved.

A lane never weakens a collision. Two items in different lanes that touch the same collision key still serialize.

A lane never grants repository, provider, merge, deploy, credential, execution, or protected-effect authority.

## Lane metadata and durable work identity

Lane placement is routing/scheduling metadata. It is intentionally **not copied into the selected work payload** used by the operator bridge.

The admission-plan digest binds the lane assignment map and therefore binds the scheduling decision. But moving an otherwise identical subject to a different lane does not silently redefine the downstream durable work subject or widen its execution authority.

This separation is deliberate:

- work identity answers **what work is this?**
- lane identity answers **which progress stream is allowed to carry it right now?**

## Capacity model

The planner exposes four independent ceilings:

- `max_parallel`: global in-flight selection ceiling;
- `max_per_lane`: optional lane ceiling;
- `max_per_identity`: worker/owner fairness ceiling;
- `max_per_family`: subsystem/family concentration ceiling.

A blocked or saturated lane does not block unrelated eligible lanes. The scheduler continues scanning deterministic candidates and admits work that remains within every global invariant.

## Local task monitor

The Windows task supervisor already records an optional `lane`. V1 lane handling adds:

- `project-runner task-status --lane <lane>`;
- `project-runner task-history --lane <lane>`;
- aggregate live counts by lane and runtime state.

Task liveness is also fenced against PID reuse. When a supervisor recorded its process creation time, a later process with the same PID but a different creation time is classified `PID_REUSED`, not `RUNNING`.

If process identity cannot be verified, the state is `IDENTITY_UNVERIFIED`; reconciliation does not archive that record merely because identity readback failed.

## Retry and uncertainty semantics

Lanes do not create exactly-once execution. Project Runner continues to rely on durable work identity, fencing tokens, expected-state checks, post-effect readback, and explicit reconciliation.

A result that may have happened but cannot be proven remains uncertain and is not blindly replayed.

## Research basis

This design intentionally borrows mature scheduler concepts without importing their authority models:

- Apache Airflow separates global parallelism from per-DAG/task/pool concurrency and priority.
- Kubernetes API Priority and Fairness isolates flows with distinct concurrency limits so one flow cannot starve unrelated flows.
- Celery routes work to named queues/workers while keeping routing distinct from task meaning.
- Temporal documents at-least-once delivery/retry semantics and recommends idempotent handlers rather than assuming a single physical execution.

Project Runner keeps its stricter existing collision, currentness, evidence, and authority boundaries around those scheduling ideas.

## Non-goals

V1 does not:

- create autonomous protected-effect authority;
- make lane labels security boundaries;
- promise hard CPU/GPU/process isolation;
- make task execution exactly once;
- replace durable queue/lease/fencing state;
- infer that separate chats are durable workers;
- let lane priority override collision or authority checks.
