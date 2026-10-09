# Authenticated, read-only local lane source — candidate V1

This branch connects the PR24 local checkout scanner and PR25 bounded
multi-repository planner to existing `GitHubRepositoryCatalog` reads and
`load_execution_nodes`. No new credentials are created or persisted.

The caller supplies a currently authenticated GitHub catalog, an exact
registered checkout index, a node manifest, independently confirmed
physical worker slot ceiling, and current observed/delegated subject IDs.
The adapter requires a nonempty token before scanning, enumerates owned
repositories from the authenticated owner's API, reads each registered
owned default ref's GitHub commit SHA, validates ref identity, commit type
and forty-hex SHA, then calls the fail-closed lane planner.

Capacity must never exceed the sum of enabled manifest node slots.
The qualified-slot ceiling, current subject/delegation state, actual machine
resources and token's authorization scope are host responsibilities.
No model, branch, GitHub mutation, or protected effect is dispatched.

The Lappy authenticated read-only evidence is stored privately at
`D:\VERA\portal-task-state\portal-multilane-census-v2-20261009\AUTHENTICATED_LANE_PROBE.json`.
It covers six named checkouts at observed exact heads. One local slot and
conservative repo-level delegated holds yield one candidate. An open Draft
PR alone does not prove that every path in a repo is delegated; before
real admission, resolve exact subject/path collisions with live owners.

> HOSTILE REVIEWER: An authenticated GitHub response, alone, cannot make
> an untrusted caller-supplied node capacity or delegation list true.
>
> ACCEPTED. This layer binds the GitHub observations and manifest without
> promoting them into independent attestation. The deployed PR22 static
> wave remains unchanged. A separately qualified resident integration must
> fence the exact session generation and reconcile holds before dispatch.

Installation, auto-refill, concurrent model workers, rollback qualification,
merge, publication, paid compute, provider/trust changes and PR14 cutover
are explicitly out of scope for this source PR.
