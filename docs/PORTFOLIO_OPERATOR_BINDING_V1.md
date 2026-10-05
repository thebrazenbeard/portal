# Portfolio Operator Binding V1

Status: source-only crosswalk / fail-closed bridge prerequisite.

This layer does not execute work and does not grant repository, queue, lease, fencing, merge, deployment, installation, credential, permission, or destructive-effect authority.

## Purpose

The portfolio corpus/wave and Operator registry are different durable scheduling surfaces.

The corpus covers the wider portfolio. Operator currently owns a smaller registry of executable projects. A corpus subject therefore cannot be handed to Operator by matching names or assuming that repository membership implies current Operator authority.

V1 cross-binds repository subjects only when all of these are true:

1. the advancement wave exactly validates against the corpus;
2. exactly one Operator project owns the exact repository;
3. Operator project id equals corpus subject id;
4. Operator visibility equals corpus visibility;
5. Operator scheduling state is SCHEDULABLE.

Anything else is HELD.

Workstreams are also HELD until an explicit multi-project Operator binding contract exists.

## Why this precedes queue/fencing integration

Operator's queue layer already has durable queue claims, collision-key overlap checks, lease expiry, and fencing tokens.

Those controls apply to concrete registered Operator frontiers. They must not be used to launder a wider corpus subject into Operator authority.

The next bridge may only create/consume queue work for subjects returned as BOUND by this crosswalk, and it must still revalidate:

- exact source currentness;
- current Operator registry digest;
- exact plan/wave digest;
- review/effect ceilings;
- queue collision claim/fencing state.

## Current public-corpus findings

The executable report intentionally exposes drift rather than correcting it automatically. Examples on the current source cut include:

- exact bindings such as `project-runner` and `discovery`;
- visibility divergence for `vera` and `vera-control-plane`;
- unregistered corpus repositories such as `project-achilles`;
- public workstreams held pending an explicit multi-project binding contract.

## CLI

```text
project-runner portfolio-operator-bindings
```

The result includes corpus and Operator-registry SHA-256 bindings plus a BOUND/HELD decision for every public wave subject.

## Claim ceiling

`EXACT_CORPUS_TO_OPERATOR_REGISTRY_CROSSWALK_ONLY__NO_QUEUE_CLAIM_OR_EXECUTION_AUTHORITY`
