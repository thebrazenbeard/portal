# Reasoning Specialist Delegation V1

Status: DESIGN SOURCE ONLY / NOT IMPLEMENTED / NOT RUNTIME-QUALIFIED

## Purpose

P.O.R.T.A.L. is the portfolio orchestration and host-interface layer. Rezon is the reasoning-method and worker-routing research authority.

The 2026-10-07 High / Work Ultra / Work Max comparison shows a useful future composition point between them:

```text
human / Vera / High coordinator
    |
    v
P.O.R.T.A.L. bounded work packet
    |
    v
Rezon routing decision
    |
    +--> deterministic worker
    +--> ordinary reasoning worker
    +--> supported Work Ultra specialist
    +--> supported Work Max reviewer
    |
    v
artifact + execution receipt
    |
    v
P.O.R.T.A.L. reconciliation
    |
    v
Vera / coordinator integration
```

This is delegation across explicit execution surfaces. It is not a mechanism for changing the reasoning tier of an existing chat.

## Empirical basis

The controlled WorkLaptop experiment used the same repository stress-test prompt.

A companion tracer observed:

- Desktop High: 0 MXC launches and 2 new established Codex TLS connections;
- Desktop Work Ultra: 59 MXC launches and 73 new established Codex TLS connections.

Ultra and Firefox Work Max independently reproduced central P.O.R.T.A.L. control defects, including finally held work remaining executable and an in-flight continuation overwriting a newer STOP.

Local telemetry did not expose Max's server-side cloud worker topology.

These observations establish that the Desktop Work path can have materially different local orchestration behavior. They do not establish that socket/process fanout grants a reasoning tier or that every process/socket is an agent.

## Responsibility boundary

### Rezon owns

- reasoning operator classification;
- reasoning-class requirement;
- specialist-worker eligibility;
- independence metadata;
- expected-information-gain policy;
- result-quality verification semantics.

### P.O.R.T.A.L. owns

- exact repository/source subject binding;
- durable command/session state;
- work packet identity;
- route/adapter currentness;
- resource/capability admission;
- attempt ownership;
- STOP/HOLD currentness;
- effect ceiling;
- execution receipts;
- ambiguous-attempt reconciliation.

### Vera owns

- cross-project intent;
- current user instruction/correction;
- governance and authority;
- final integration/application of reasoning products.

## Candidate packet extension

A future P.O.R.T.A.L. work packet may need fields equivalent to:

```text
reasoning_requirement
preferred_product_surfaces[]
independence_requirement
context_manifest[]
result_artifact_contract
reasoning_authority_ceiling
```

These fields express requirements. They do not fabricate provider entitlements.

An adapter may select a Work Ultra/Max surface only when that surface is supported, currently available, and callable through an authorized interface.

## Dispatch rules

Before crossing an effect boundary, bind:

- exact subject and exact head/version;
- literal task;
- canonical context manifest;
- requested operator/reasoning class;
- selected adapter and observed surface;
- tool/environment requirements;
- resource reservation;
- attempt identity;
- authority/effect ceiling.

Changing a correctness-relevant semantic input requires explicit supersession or a new dispatch identity.

## Result rules

A specialist result should be stored as a separate reasoning artifact with:

- request/dispatch identity;
- observed surface/model/reasoning label if exposed;
- start/end timestamps;
- artifact hashes/locators;
- findings/claims;
- tests/checks performed;
- unresolved items;
- failure state;
- exact source versions;
- verification state.

A higher-cost worker cannot directly promote repository state, authority, installation, deployment, or completion.

## Hard blocker from the 2026-10-07 stress test

Do **not** implement automatic high-reasoning delegation on top of the currently reproduced control defects.

Before the bridge is eligible for implementation, P.O.R.T.A.L. must close and regression-test at least:

1. finally held queued work cannot cross a later attempt boundary;
2. a newer STOP cannot be overwritten by an in-flight continuation;
3. replay/idempotent attempt-marker handling cannot be confused with fresh execution ownership;
4. bounded selection cannot indefinitely starve unrelated ready work;
5. exact capability requirements are part of cached-result identity.

Reasoning escalation increases the cost and possible external consequences of duplicate or stale execution, so these are preconditions rather than unrelated cleanup.

## Failure states

At minimum preserve:

```text
SURFACE_UNAVAILABLE
RESOURCE_LIMIT
ROUTE_STALE
CONTEXT_STALE
STOPPED
HELD
ATTEMPTED_UNKNOWN
RESULT_UNVERIFIED
INDEPENDENCE_UNSATISFIED
```

Unavailable high-cost reasoning must not silently become consensus or be reported as if the requested specialist ran.

## Telemetry

Tattler/process telemetry may be attached to a receipt as diagnostic evidence, but it is not a capability oracle.

```text
observed MXC fanout -> diagnostic provenance
observed socket fanout -> diagnostic provenance
selected product surface -> adapter/provider observation
reasoning label -> exposed surface metadata
authority -> separate governed input
```

## Promotion gate

This design can move toward implementation only after:

- the Rezon escalation contract is qualified;
- the P.O.R.T.A.L. execution-control defects above are fixed and stress-tested;
- a supported callable specialist surface exists;
- a mock adapter proves the lifecycle first;
- ambiguous completion and resource settlement are tested;
- exact-head replay/recovery tests exist;
- no provider/entitlement spoofing is required.

## Claim ceiling

This design does not prove or activate Ultra/Max callability, provider access, a live adapter, deployment, installation, model identity, hidden chain-of-thought, or server-side worker count.
