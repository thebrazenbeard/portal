# Portfolio Discovery Policy V1

Status: EXODUS_CANDIDATE
Last discovery cut: 2026-09-20

Project Runner must not rely on a hand-maintained chat list as the complete project inventory.

A fresh portfolio pass discovers repositories from the authorized GitHub account/installation and classifies them before scheduling work.

Recommended classes:

- ACTIVE_PROJECT — durable current work exists.
- ACTIVE_INFRASTRUCTURE — shared infrastructure or execution/control surface.
- ACTIVE_WORKER_HOME — durable home of a named worker/collective.
- PLACEHOLDER — repository exists but has no current runnable implementation.
- HISTORICAL_ARCHIVE — useful provenance, not current execution.
- POSSIBLE_DUPLICATE_OR_PREDECESSOR — overlapping identity/source requires reconciliation before use.
- NEEDS_CLASSIFICATION — evidence is insufficient to promote into the active portfolio.

Discovery must preserve repository-local autonomy. A newly discovered repository is not automatically coupled to Project Runner, granted write authority, or added to a shared architecture spine.

`DISCOVERABLE != COUPLED`

`COORDINATED != AUTHORIZED`

`EXECUTABLE_ROUTE != TARGET_AUTHORITY`

## Current discovery evidence

The owner-repository inventory was freshly enumerated again during repository-estate reconciliation on 2026-09-20:

- repository count: **58**
- archived repositories: preserved as historical/archive candidates rather than silently scheduled
- private repository identities/content: retained in the private portfolio checkpoint rather than mirrored into this public repository

The previous 2026-09-17 inventory contained 53 repositories and is historical evidence. An earlier 2026-09-20 cut contained 57 repositories and is also historical evidence; one additional repository existed by the estate-reconciliation refresh.

The earlier 57-repository cut had already discovered four repositories that did not exist in the 2026-09-17 cut:

- `thebrazenbeard/mosaic`
- `thebrazenbeard/testament`
- `thebrazenbeard/driftguard`
- `thebrazenbeard/discovery`

Mosaic and Testament were already represented in the composed Exodus candidate before this refresh. DriftGuard and Discovery are public repositories with durable current project material, so they are added to the public-safe seed registry on this candidate branch.

The public seed is intentionally incomplete. It contains public-safe current projects plus legacy private identifiers already historically disclosed by Project Runner. It must not expand with additional private project identifiers merely because discovery can see them.

## Complete private inventory

The complete 58-repository classification belongs in the private estate/portfolio checkpoint and, when used by Project Runner, in an explicitly selected external registry bound by exact SHA-256.

That private registry is a **replacement** runtime registry, not a hidden merge into public source.

Every private external project record must explicitly declare:

- assignment scope;
- review scope;
- family;
- scheduling state.

`HELD`, `ARCHIVED`, `DORMANT`, `SENSITIVE_HELD`, and `DECISION_HELD` remain observable but non-schedulable.

## Discovery census currentness

Project Runner PR #23 is bound to Discovery census bytes with count 57 and all-name SHA-256 `43dfda1fa3dd24dec39e2aa345d93ab192dbda777433feae384da632b3d008dd`. Discovery's own census contract states that a repository-count or inventory-digest change invalidates claims that those exact bytes are current. The live estate count is now 58, so those exact census bytes remain historical interoperability evidence, not current portfolio state.

A future Discovery-census consumer must bind freshly generated Discovery bytes and undergo fresh exact-head review. Project Runner does not silently substitute remembered or newly observed inventory into an old exact binding.

## Refresh rule

Repository discovery is a starting observation, not timeless truth.

Before portfolio scheduling or mutation:

1. re-enumerate authorized repositories;
2. fresh-read exact repository/ref state for the target;
3. load the exact private registry bytes when private projects are needed;
4. verify the configured SHA-256;
5. re-check scheduling state, capability, target authority, collision state, and dependencies.

A chat transcript or remembered project list never substitutes for discovery/currentness.
