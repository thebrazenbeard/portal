# Portfolio Operator Bound-Plan Bridge V1

Status: source-level claim bridge / no backend execution / no protected effects

## Purpose

This layer connects a reviewed portfolio admission plan to Project Runner's durable
lease/fencing machinery without turning the admission planner into authority.

The bridge performs one transition only:

`BOUND PLAN SELECTION -> DURABLE CLAIMED WORK + FENCING TOKEN`

It does not run the selected project's backend operation.

## Preconditions

A subject can be claimed only when all of the following survive exact validation:

1. the plan is `PORTFOLIO_WAVE_ADMISSION_PLAN_V1`;
2. `execution_authority=false`;
3. `protected_effects_authorized=false`;
4. the canonical plan SHA-256 recomputes exactly;
5. the exact wave-file SHA-256 matches the plan's wave binding;
6. wave id, generation timestamp, and corpus binding match the source wave;
7. the local public corpus bytes reproduce the Git blob SHA bound by the wave;
8. the selected item exactly reproduces its source-wave fields;
9. the selected item is a queued repository subject with `SOURCE_ONLY` ceiling;
10. the corpus gives exactly one non-archived repository and explicit default branch;
11. the Operator registry gives exactly one schedulable project binding with matching
    id, repository, and visibility;
12. the repository is present in the caller's explicit allowed-repository set.

Any mismatch fails before durable claim.

The claim-scope allowlist is **not** proof of repository write authority, a credential,
or a principal grant. It only constrains which already-bound repository subject this
claim-only command may place into local durable Operator state. Protected repository
effects remain separately unauthorized.

## Fresh currentness

The bridge never guesses `main`.

It obtains the selected repository's default branch from the bound corpus record and
performs a read-only GitHub `READ_REF` through an exact repository/ref authority
grant. The returned value must be a lowercase 40-hex commit.

That exact commit becomes the immutable `ExactSubject` stored in the durable work
record.

## Durable claim

The bound work unit contains:

- exact repository/ref/commit;
- plan SHA-256;
- wave SHA-256;
- selected admission metadata;
- exact Operator project id and Operator registry SHA-256;
- collision keys;
- explicit `execution_authority=false`;
- explicit `protected_effects_authorized=false`.

The root budget and immutable work state are seeded atomically. A second durable
transaction then:

- consumes the active/backend-job reservation;
- acquires a fresh lease;
- creates/increments the fencing token;
- marks work `CLAIMED`;
- creates an execution-attempt journal row with no backend result.

The bridge returns the fence receipt. It does not advance the work to `RUNNING`.

## Crash, replay, and lease-expiry behavior

The lineage id is deterministic over the canonical plan digest and subject id.

If the process dies after root initialization but before admission, replay verifies
the exact immutable root and resumes admission instead of orphaning the lineage.

If admission committed but the caller did not receive the receipt:
- replay by the same holder while the lease is active is idempotent and returns the
  existing fence;
- a different holder cannot steal an active lease;
- after lease expiry, claim-only work may be re-fenced only when it remained
  `CLAIMED`, carries `execution_authority=false`, and has no backend result,
  verification, or prior reconciliation;
- that expired fence is durably recorded as `NO_EFFECT_CONFIRMED` before the new
  fencing token is issued;
- reclaim does not consume the root backend/active budget a second time.

If the repository head changes between claims, the exact work fingerprint changes.
The bridge fails closed rather than silently replacing the exact subject inside an
existing lineage. Supersession/currentness migration remains a separate frontier.

A generic durable executor also rejects any admitted work whose payload explicitly
carries `execution_authority=false`; `CLAIMED` alone is not backend authority.

## Explicit non-authority

A successful claim proves only that:

- reviewed scheduling evidence was bound;
- current repository state was re-read;
- Operator registry ownership was exact;
- caller claim scope allowlist included the subject;
- a durable lease/fencing token was acquired.

It does **not** prove or authorize:

- backend execution;
- branch/file mutation;
- merge;
- deployment or installation;
- credential/permission mutation;
- runtime/effect completion;
- review completion beyond what the bound plan records.

## CLI

The source exposes:

```text
project-runner portfolio-wave-claim \
  --plan /path/to/plan.json \
  --subject-id project-runner \
  --state-db /path/to/operator.sqlite3 \
  --holder operator-instance-1 \
  --lease-ttl 60 \
  --allowed-repository thebrazenbeard/project-runner
```

The default wave, corpus, and Operator registry paths point to the repository's
canonical source files. External paths can be supplied explicitly.

The result reports `backend_execution_performed=false` together with the exact
head, work fingerprint, lineage id, fencing token, and generation numbers.

The bridge's `protected_effects_authorized=false` flag remains distinct from
execution authority: it does not redefine all possible read-only backend execution
as a protected effect. This bridge sets both flags false, but the durable executor's
hard stop is the explicit `execution_authority=false` boundary.

## Claim ceiling

`BOUND_PLAN_TO_DURABLE_CLAIM_ONLY__FRESH_CURRENTNESS_AND_FENCE__NO_BACKEND_EXECUTION_OR_PROTECTED_EFFECT`
