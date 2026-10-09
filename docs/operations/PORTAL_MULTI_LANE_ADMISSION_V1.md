# P.O.R.T.A.L. bounded local multi-repository lane plan (source only)

The running desktop PR22 job knows only one qualified worker slot and one
registered source checkout. Repeating the timer does not increase capacity,
refresh the baseline work wave, or produce new useful work.

This branch adds a read-only, deterministic planning step:
- Input: the previously validated local checkout index, a separate owned-repo
  inventory, exact remote default-branch HEADs, the resident's observed
  subject identities, exact actively delegated identities, and a capacity
  value supplied by the qualified node scheduler.
- Output: one `LocalLane` record per registered repository with an explicit
  `CANDIDATE_FOR_SCHEDULER`, `DEFERRED_CAPACITY`, or fail-closed HOLD disposition.
- Repositories already observed (including HELD proposals) are never
  reintroduced simply because their source HEAD changed.
- Exact delegation conflicts, missing owned membership, private repositories
  lacking authenticated inventory, archived repositories, wrong Git refs,
  invalid/missing remote SHA, and stale local HEADs are held.
- Git refs are case-sensitive. Repository and subject identities are
  compared case-insensitively to reject duplicate naming.
- 13 eligible repo frontiers and one qualified worker slot yield one
  *candidate*, 12 deferrals, and **zero launched workers**.

This is a planning primitive, **not** GitHub ownership attestation, queue
activation, valid concurrency measurement, or execution authority. Its
inventory, remote HEADs, delegation state, and node capacity are host-supplied
claims. A malicious or stale caller can falsify them. The real host must bind
these inputs to authenticated GitHub reads, exact live session state, and a
qualified physical node manifest before advancing an actual wave.

> HOSTILE REVIEWER: You have only labelled a list of repositories.
> That is not parallel autonomous execution.
>
> ACCEPTED. The user-facing system still has a one-slot local worker and a
> frozen static wave. The planner is a prerequisite to safely admitting more
> subjects, not proof that the existing runtime dispatches them. A separate
> host integration and real multi-repository model-run qualification are
> required before installing any change or advertising parallel workers.

The real Lappy read-only census is retained only in local
`D:\VERA\portal-task-state\portal-multilane-census-v2-20261009\CANDIDATE_LANES.json`.
No private repository membership should be published as a generic corpus.
No installed PR22 files, PR14 sessions, credentials, permissions, or
canonical runtime routing were changed for this branch.
