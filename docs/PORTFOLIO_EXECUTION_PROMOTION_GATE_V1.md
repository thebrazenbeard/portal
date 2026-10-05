# Portfolio Execution Promotion Gate V1

Status: source-level CLAIMED -> RUNNING gate / exact-fence / exact-head / review-freshness / separated authority

This layer is intentionally stacked after the bound-plan claim bridge.

It does not turn a successful claim into execution authority. It creates a separate
promotion record only after independently rechecking the claim, lease, source,
review, and authority inputs.

## State transition

The gate owns exactly one durable transition:

```text
CLAIMED -> RUNNING
```

Promotion and backend execution are separate operations.

A successful promotion does not itself call a backend.

## Preconditions

Promotion requires all of the following:

1. the durable work exists and is exactly `CLAIMED`;
2. the work is the bound-plan claim schema;
3. the claim itself still carries `execution_authority=false` and
   `protected_effects_authorized=false`;
4. the exact lease holder and fencing token match the durable claim;
5. the lease is incomplete and unexpired;
6. the execution-attempt holder and work generation cross-bind to the claim;
7. no backend result, verification, or reconciliation exists for that fence;
8. a signed review-evidence document binds the exact subject, plan, work
   fingerprint, required review gate, and one reviewer identity named by the
   claim;
9. that review has state `EXECUTION_PROMOTION_REVIEWED` and is fresh;
10. a separately signed execution-authority grant binds the exact lineage,
    fingerprint, fence, subject, operation, and exact head and is fresh;
11. the requested execution operation exactly equals the bound wave action;
12. the execution effect class does not exceed the bound wave effect ceiling;
13. any protected effect has a **separate** protected-effect authority grant
    binding the same exact claim/fence/effect class;
14. the repository default branch is read live and still equals the claim's
    stored exact commit.

The current public corpus has only `NO_EFFECT` and `SOURCE_ONLY` ceilings.
V1 permits:

- `NO_EFFECT` -> `NO_PROTECTED_EFFECT` only;
- `SOURCE_ONLY` -> `NO_PROTECTED_EFFECT` or `SOURCE_WRITE`.

Merge, deploy, install, credential/permission changes, and destructive effects
therefore fail closed even if an otherwise valid protected-effect grant is
present.

## Evidence authenticity

V1 uses HMAC-SHA256 to authenticate three evidence classes:

- review evidence:
  `PROJECT_RUNNER_REVIEW_EVIDENCE_KEY`;
- execution authority:
  `PROJECT_RUNNER_EXECUTION_AUTHORITY_KEY`;
- protected-effect authority:
  `PROJECT_RUNNER_PROTECTED_EFFECT_AUTHORITY_KEY`.

The execution and protected-effect keys are deliberately separate. Possession of
one cannot produce a valid grant for the other authority class.

No key is created, installed, rotated, or configured by this package. Missing key
custody fails closed.

A digest alone is not treated as authority; the HMAC verification must succeed.

## Durable promotion receipt

Promotion inserts an `execution_promotions` row and changes the work state to
`RUNNING` in one SQLite transaction.

The durable promotion row binds:

- lineage id;
- work fingerprint;
- fencing token and holder;
- repository/ref/exact head;
- operation and effect class;
- review digest + expiry;
- execution-authority digest + expiry;
- protected-effect digest + expiry when applicable;
- pre-promotion and post-promotion work generations;
- promotion timestamp;
- canonical promotion SHA-256.

The returned receipt must exactly equal the durable row. A caller cannot extend
an expiry or substitute a different head/effect class while retaining the old
promotion digest.

If promotion committed but the caller lost the response, replay is idempotent only
for the same exact fence, holder, signed review, signed execution grant, signed
protected-effect grant (when applicable), operation, effect class, and still-current
live head. The existing durable receipt is returned; no second RUNNING transition is
created.

## Execution-time recheck

Backends for promoted portfolio work use `execute_promoted()`, not
`execute_admitted()`.

Immediately before calling the backend, the governed path rechecks:

- durable promotion receipt integrity;
- work is still exactly `RUNNING`;
- current lease holder/token;
- lease expiry;
- review freshness;
- execution-authority freshness;
- protected-effect-authority freshness when applicable;
- live repository head still equals the exact promoted head.

The backend is not called when any check fails.

After a backend call returns, the existing execution-attempt journal records the
result under the same exact fencing token.

## External race ceiling

Git branch movement cannot be locked atomically with the local SQLite transaction.

The gate therefore provides two live head reads:

- once immediately before promotion commit;
- again immediately before backend invocation.

A promoted backend that mutates an external branch must still use the receipt's
exact head as its own compare-and-swap/precondition. Project Runner's GitHub
backend already supports exact-head preconditions for source mutation.

The promotion gate does not claim that a branch can never move after the final
read.

## Review freshness

A favorable historical review is insufficient.

The review evidence must:

- name a reviewer identity required by the claim;
- match the exact claim review gate;
- bind the exact head and work fingerprint;
- carry `EXECUTION_PROMOTION_REVIEWED`;
- have a valid signed freshness interval at promotion time.

Execution rechecks the stored review expiry again before backend invocation.

## Authority separation

Execution authority and protected-effect authority are structurally different
documents verified by different keys.

Execution authority is always required.

Protected-effect authority is required only when the effect class is not
`NO_PROTECTED_EFFECT`.

An effect grant cannot substitute for execution authority. An execution grant
cannot authorize a protected effect by itself.

## CLI

```text
project-runner portfolio-wave-promote \
  --state-db /path/to/operator.sqlite3 \
  --lineage-id <claim lineage> \
  --work-fingerprint <claim fingerprint> \
  --fencing-token <claim token> \
  --holder <claim holder> \
  --review /path/to/review.json \
  --execution-grant /path/to/execution-grant.json \
  [--effect-grant /path/to/protected-effect-grant.json]
```

The command performs promotion only. It reports
`backend_execution_performed=false`.

## Explicit non-claims

This package does not:

- create or grant authority keys;
- assert that any current user/person has execution authority;
- create a real execution grant for a live project;
- merge or deploy;
- mutate credentials/permissions;
- execute a protected backend effect;
- establish cross-database distributed fencing;
- replace exact backend compare-and-swap requirements.

## Claim ceiling

`CLAIMED_TO_RUNNING_PROMOTION_GATE__EXACT_FENCE_HEAD_REVIEW_AND_SEPARATED_AUTHORITY__BACKEND_RECHECK_REQUIRED`
