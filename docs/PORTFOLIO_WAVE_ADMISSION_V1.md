# Portfolio Wave Admission V1

Status: deterministic scheduling/admission layer only. This artifact grants no repository, provider, merge, deployment, installation, credential, permission, or destructive-effect authority.

## Why this layer exists

The corpus-wide advancement wave deliberately covers every public subject. Coverage is not concurrency authority.

A wave with dozens of QUEUED subjects needs a separate bounded admission step before any durable operator or worker route may consume it. The V1 planner therefore selects only a small deterministic slice while preserving the rest as deferred work.

## Required budget

There is no implicit concurrency default.

The caller must provide:

- `max_parallel`: total subjects admitted in this planning pass;
- `max_per_identity`: maximum admitted subjects for one lead identity;
- `max_per_family`: maximum admitted subjects from one portfolio family;
- zero or more already occupied collision keys.

The planner rejects invalid budgets instead of inventing one.

## Collision semantics

Repository subjects reserve `repository:<owner/repo>`.

Portfolio families are governed separately by `max_per_family`. Family membership is not treated as a repository collision; it is an explicit concurrency budget so related repositories can be serialized without pretending they are the same durable surface.

Workstreams reserve every repository-like durable surface they name. Non-repository surfaces are reserved as `surface:<normalized surface>`.

This means a workstream that includes `thebrazenbeard/project-runner` collides with direct Project Runner repository work in the same planning pass.

The planner may also receive collision keys already held by another execution path. Those keys are treated as occupied and are not reclaimed or overridden.

## Selection semantics

Candidates are ordered deterministically by:

1. portfolio priority P0 through P4;
2. lead identity;
3. subject kind;
4. subject id.

An item is not selectable when:

- its execution state is not `QUEUED`;
- its action is inert/preserve-only;
- its effect ceiling is `NO_EFFECT`;
- its effect ceiling exceeds `SOURCE_ONLY`;
- the global budget is exhausted;
- its lead identity has exhausted its identity budget;
- any collision key is already reserved.

Selection does not execute anything. It is only an admission plan.

## CLI

Example:

```text
project-runner portfolio-wave-plan \
  --max-parallel 6 \
  --max-per-identity 1 \
  --max-per-family 1 \
  --occupied-collision-key repository:thebrazenbeard/vera-mono
```

Output explicitly carries:

- `execution_authority: false`;
- `protected_effects_authorized: false`;
- SHA-256 of the exact wave file bytes plus wave/corpus binding metadata;
- a canonical SHA-256 binding over the complete plan payload;
- selected subjects with reviewer identities, review gate, effect ceiling, frontier, source status, and collision keys;
- deferred subjects and deterministic reasons.

The plan digest is evidence of exactly what was planned. It is not a lease, authority grant, currentness proof, or effect receipt.

## Operator handoff boundary

The next integration layer may let Operator consume an admitted subject, but it must first verify the wave digest and canonical plan digest, then re-establish:

- exact current source;
- target repository/ref authority;
- durable collision/fencing ownership;
- effect-class ceiling;
- reviewer/currentness evidence.

This planner is not a lease and must never be treated as one.

## Claim ceiling

`DETERMINISTIC_BOUNDED_WAVE_ADMISSION_ONLY__NO_EXECUTION_OR_EFFECT_AUTHORITY`
