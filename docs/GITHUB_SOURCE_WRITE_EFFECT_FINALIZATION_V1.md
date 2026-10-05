# GitHub SOURCE_WRITE EFFECT_CONFIRMED Finalization V1

Status: terminal verification after conclusive repository-effect reconciliation

## Purpose

This layer advances a promoted GitHub `SOURCE_WRITE` attempt from a durable,
conclusive `EFFECT_CONFIRMED` reconciliation into Project Runner's terminal
verification lifecycle.

It does not replay the backend and does not create any new repository effect.

## Preconditions

Finalization requires all of the following to remain true:

1. exact durable promotion exists and its integrity digest verifies;
2. effect class is exactly `SOURCE_WRITE`;
3. the recorded backend result is the original failed `OUTCOME_UNKNOWN`;
4. that result carries exactly the candidate commit SHA and candidate blob SHA;
5. latest durable reconciliation exists, its journal digest verifies, and its
   outcome is exactly `EFFECT_CONFIRMED`;
6. the exact promotion-bound execution request is present and its digest still
   matches the promotion;
7. repository, ref, and expected pre-write head still match the promotion;
8. the exact fence/holder is still current and uncompleted;
9. the lease has not expired.

A missing or merely `INDETERMINATE` reconciliation is never enough.

## Independent effect verification

The finalizer does not trust the prior reconciliation alone.

It re-reads:

```text
B0 branch head
-> target file at exact B0 commit
-> B1 branch head
```

and requires:

- B0 == candidate commit SHA;
- B1 == B0;
- target blob SHA == candidate blob SHA;
- exact UTF-8 content == the promotion-bound intended content.

Head movement, blob drift, content drift, or read failure prevents terminal
finalization.

## Fence revalidation

The active fence is checked before GitHub readback.

After readback, the clock is sampled again and the lease must still be active.

After the first exact candidate readback, the durable store atomically advances
the work from `RUNNING` to `VERIFYING` under the same exact active fence.

That transition is idempotent at the one expected successor generation, so a
lost response after entering `VERIFYING` can resume without backend replay.

The finalizer then performs a second independent exact candidate readback while
the durable work is already `VERIFYING`. Only after that second stable
commit/blob/content check does `finalize_terminal_verification()` revalidate
the same lease/fencing token and atomically:

- mark the lease complete;
- move recursive work from `VERIFYING` to `COMPLETE`;
- append terminal verification evidence.

The qualified happy-path generations are therefore:

`RUNNING gN -> VERIFYING gN+1 -> COMPLETE gN+2`.

If the terminal transaction commits but the caller loses the response, a retry
reconstructs the terminal receipt only when the exact completed generation,
completed holder/fencing token, validated terminal verification record,
promotion, execution request, and EFFECT_CONFIRMED reconciliation all still
cross-bind. The returned receipt is marked `finalization_replayed=true`.

That receipt replay is historical/idempotent. It does **not** perform another
GitHub read and therefore does not assert that the candidate remains current at
replay time.

Thus one source readback cannot jump directly from execution to completion, a
stale or replaced fence cannot finalize, and response loss after terminal commit
cannot cause backend or source-effect replay.

## Result semantics

The original backend result remains `OUTCOME_UNKNOWN`; it is not rewritten.

The conclusive reconciliation plus independent readback is the verification
basis for terminal `COMPLETE`.

The terminal reason explicitly records that no backend replay occurred.

The CLI result also states:

- `backend_replayed=false`;
- `deployment_effect_claimed=false`;
- `installation_effect_claimed=false`.

## Explicit ceiling

This path proves only that the exact promoted GitHub repository source effect is
currently present and has been terminally verified under the original fence.

It does not prove or imply:

- deployment;
- installation;
- runtime activation;
- downstream webhook completion;
- package publication;
- release publication;
- credential or permission effects;
- any backend effect beyond the exact source write.

## Claim ceiling

`GITHUB_SOURCE_WRITE_EFFECT_CONFIRMED_TO_COMPLETE__EXACT_FENCE_AND_CANDIDATE_REVERIFIED__NO_BACKEND_REPLAY_OR_DOWNSTREAM_EFFECT_CLAIM`
