# GitHub SOURCE_WRITE Runtime Qualification V1

Status: read-only runtime qualification + read-only OUTCOME_UNKNOWN reconciliation

WoWSQL is retired under current live project authority. This qualification does
not consult WoWSQL and does not assume or nominate a successor currentness
backend.

## Purpose

This layer qualifies the real GitHub route needed by the promoted
`SOURCE_WRITE` adapter without exercising source-write capability.

It also adds read-only reconciliation for a source-write attempt already
journaled as `OUTCOME_UNKNOWN`.

## Runtime qualification

`github-source-write-runtime-qualify` requires the configured
`PROJECT_RUNNER_GITHUB_TOKEN` and performs only:

1. exact branch-ref read;
2. exact commit -> tree read;
3. GraphQL schema introspection;
4. branch-ref reread.

The probe requires the GraphQL schema to expose:

- mutation `updateRefs`;
- `UpdateRefsInput.repositoryId`;
- `UpdateRefsInput.refUpdates`;
- `RefUpdate.name`;
- `RefUpdate.beforeOid`;
- `RefUpdate.afterOid`;
- `RefUpdate.force`.

The two branch reads must match. A moving ref fails qualification rather than
mixing a tree/schema observation with a different current head.

The result always reports `write_exercised=false`.

The command never creates Git objects, calls `updateRefs`, writes a file, or
moves a ref.

## GitHub API contract

GitHub's current GraphQL documentation describes `updateRefs` as atomic and
states that `RefUpdate.beforeOid` requires the ref to point to the supplied OID
before updates are performed.

Runtime introspection verifies that the configured endpoint/token actually sees
the schema fields required by this source-controlled adapter.

Schema visibility is not write authorization. No mutation is executed by the
qualification command.

## OUTCOME_UNKNOWN reconciliation

A promoted GitHub source write can return `OUTCOME_UNKNOWN` when ref publication
or post-publication readback cannot be proven.

The adapter now journals the candidate commit SHA and candidate blob SHA with
that result.

`github-source-write-reconcile` never replays the write. It reconstructs the
durable promotion and exact execution request, verifies their digest binding,
then performs:

```text
B0 branch head
-> target file read at exact B0 commit
-> B1 branch head
```

If B0 and B1 differ, reconciliation is `INDETERMINATE`.

On a stable snapshot:

- `EFFECT_CONFIRMED`: current head equals the candidate commit and the exact
  target blob/content equals the candidate blob/content;
- `INDETERMINATE`: every other state, including a branch that currently
  matches the pre-write snapshot.

Returning to the pre-write head is **not** treated as `NO_EFFECT_CONFIRMED`,
because the candidate could have been transiently published and later reverted.
Current state alone cannot prove that no publication or webhook-visible effect
ever occurred.

The outcome is persisted through Project Runner's existing durable
`execution_reconciliations` journal.

## Lifecycle behavior

For a recorded `OUTCOME_UNKNOWN` only:

- `EFFECT_CONFIRMED` remains non-retryable and leaves the RUNNING work/fence
  in place for later verification/finalization;
- `INDETERMINATE` remains non-retryable and may later be refined by another
  read-only reconciliation.

This GitHub source-write reconciler does not currently manufacture
`NO_EFFECT_CONFIRMED` from present repository state. A future independent
proof source could use the generic durable reconciliation state machine to
record that outcome, but only with evidence stronger than "the branch looks
unchanged now."

No other recorded backend classification is eligible for this reconciliation
path.

## Explicit non-effects

This layer does not:

- create or update a Git ref;
- create Git blobs, trees, or commits;
- mutate files;
- test write permission;
- merge or deploy;
- alter credentials or permissions;
- replay an unknown write;
- reintroduce WoWSQL.

## Claim ceiling

`GITHUB_SOURCE_WRITE_RUNTIME_READ_QUALIFICATION_AND_UNKNOWN_RECONCILIATION_ONLY__NO_WRITE_EXERCISED`
