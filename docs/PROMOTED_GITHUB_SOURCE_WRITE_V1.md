# Promoted GitHub SOURCE_WRITE Adapter V1

Status: exact-promotion source-write adapter / PUT_FILE only / no merge or deployment authority

## Purpose

This adapter is the first real backend qualified behind the
`CLAIMED -> RUNNING` execution-promotion gate.

It accepts only a `PromotedExecution` whose effect class is exactly
`SOURCE_WRITE`.

It does not accept fresh mutation parameters at execution time.

The exact write request must already have been:

1. included in the signed execution-authority document;
2. hashed canonically;
3. bound by the signed review evidence;
4. bound by the separate protected-effect authority document;
5. persisted durably in the same transaction as the RUNNING promotion.

## Request schema

The execution request uses:

```json
{
  "schema": "PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1",
  "operation": "PUT_FILE",
  "repository": "owner/repository",
  "ref": "branch",
  "expected_head": "<40-hex commit>",
  "path": "path/to/file",
  "content": "exact new UTF-8 content",
  "message": "commit message",
  "expected_blob_sha": "<existing blob sha or null>"
}
```

The canonical JSON request SHA-256 is part of the promotion receipt.

The full canonical request is stored in
`execution_promotion_requests`, keyed by lineage, work fingerprint, and
fencing token. Its digest is verified on recovery.

## Promotion binding

For `SOURCE_WRITE`, promotion fails unless:

- the execution grant contains an exact execution request;
- review evidence contains the same request SHA-256;
- protected-effect authority contains the same request SHA-256;
- the wave effect ceiling allows `SOURCE_WRITE`.

This prevents a favorable review or generic source-write authority from being
reused for a different path or content.

## Adapter checks

`PromotedGitHubSourceWriteBackend` rejects execution unless:

- effect class is exactly `SOURCE_WRITE`;
- a durable execution request exists;
- request schema is exactly `PROJECT_RUNNER_GITHUB_SOURCE_WRITE_V1`;
- operation is exactly `PUT_FILE`;
- request SHA-256 equals the durable promotion binding;
- repository, ref, and expected head equal the promotion receipt;
- path is already canonical and repo-relative (no leading/trailing slash,
  empty segment, "." segment, or ".." segment).

The adapter derives its GitHub target grant from that already-promotion-bound
request. It does not accept a broader caller-supplied target grant.

## Exact compare-and-swap

The adapter uses a Git-data write path rather than the GitHub Contents API.

The transport independently:

1. reads the branch head and requires `expected_head`;
2. reads the exact expected commit/tree;
3. traverses the exact tree to the target file;
4. for an existing file, requires regular-file mode `100644` and the exact
   `expected_blob_sha`; new files use mode `100644`;
5. creates the new blob, tree, and commit with `expected_head` as the parent;
6. publishes the new commit with GitHub GraphQL `updateRefs`, using
   `beforeOid=expected_head`, `afterOid=<new commit>`, and `force=false`;
7. reads the branch and file back;
8. requires branch readback, blob SHA, and content to equal the created objects.

The `beforeOid` comparison is the publication-time head CAS. If the branch
moves after the promotion gate's live read—or after the adapter's preliminary
read—the ref update is rejected instead of silently applying the write to a
different head.

Blob/tree/commit objects are created before ref publication. A rejected CAS can
therefore leave unreachable Git objects, but it does not move the branch ref or
publish the source change.

## Result binding

The internal GitHub backend operates on a transient request work unit, but the
adapter remaps the observed result to the **original promoted work
fingerprint**.

`execute_promoted()` journals that result under the original lineage and
fencing token.

Thus the durable execution journal remains bound to the claim/promotion rather
than to an implementation-only transient work unit.

## Failure behavior

The adapter performs no mutation when:

- effect class is not `SOURCE_WRITE`;
- durable request is missing or malformed;
- request digest does not match the promotion;
- repository/ref/head diverges from the promotion;
- branch head CAS fails;
- existing blob CAS fails;
- target authority derived from the exact request fails.

An explicit head/blob/ref-CAS rejection is returned as
`PRECONDITION_FAILED` and is a clean no-publication outcome.

If the ref-update request may have reached GitHub but its result cannot be
proven—for example, transport uncertainty or failed readback after publication—
the adapter returns `OUTCOME_UNKNOWN`. That result is journaled under the
original fence, and the governed execution path refuses a blind second backend
execution for the same attempt.

## Explicit ceiling

This adapter cannot:

- create a branch;
- merge a PR;
- deploy;
- install;
- mutate credentials or permissions;
- delete a repository or branch;
- execute an arbitrary GitHub operation.

Only exact `PUT_FILE` source writes are in scope.

## Claim ceiling

`PROMOTED_GITHUB_SOURCE_WRITE_V1__EXACT_REQUEST_HEAD_BLOB_CAS_AND_READBACK__NO_MERGE_DEPLOY_OR_CREDENTIAL_AUTHORITY`
