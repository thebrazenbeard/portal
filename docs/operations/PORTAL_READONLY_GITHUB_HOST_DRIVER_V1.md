# PORTAL — Bounded read-only GitHub ref host driver V1

Status: source candidate. This module is a constrained **external process driver**, not a resident scheduler or an independently qualified execution route.

## What it does

The `portal.host_github_ref_driver` module is an argv-only subprocess endpoint for `PortalCommandHostDriver`. It receives the canonical `PORTAL_HOST_DRIVER_REQUEST_V1` JSON envelope on stdin, checks that the repository is in an explicit command-line allowlist, validates the exact subject, adapter, route, branch, action, effect ceiling, and expected 40-hex SHA, then reads `gh api repos/OWNER/REPO/git/ref/heads/main`. No API write methods, shell invocation, source-tree mutation, merging, or deployment are exposed.

When the live GitHub ref matches, the response is `VERIFIED_COMPLETE`; a changed head is `VERIFIED_HELD` (not a false success). Transport failures, malformed JSON, duplicate fields, disallowed targets, and invalid evidence cause a nonzero subprocess exit and are handled by `PortalHostPump` as an attempted unresolved result. Result JSON echoes all five route/subject binding fields plus the original attempt ID.

## Example local command (only for a pre-qualified exact host route)

```powershell
python -m portal.host_github_ref_driver --allowed-repository thebrazenbeard/portal --allowed-repository thebrazenbeard/vera-mono --adapter-id lappy-local-readonly-gh
```

The operator must configure the outer PORTAL `PortalCommandHostDriver` argv and the exact `lappy-local-readonly-gh` adapter route. Requests must bind `route_id=local-gh:SUBJECT:main`, `action=VERIFY_EXACT_HEAD`, and `effect_ceiling=SOURCE_ONLY`. The driver is only a verification workload; it does not grant SOURCE_ONLY authority to mutate source. Do not grant an external source-writing route based on this driver.

## Qualification ceiling

The owning GitHub ref is fetched by an installed authenticated `gh` CLI through one read-only GitHub REST endpoint. A host should independently read the same ref after the driver result, preserve the exact attempt and ref evidence, and reconcile through the durable host bridge. A successful driver result is not a substitute for a qualified route, production resident host, independent reviewer, or proof that a subsequent write is authorized.

This component is intentionally constrained to default `main` refs; it is not a general task runner. Tests cover malicious/disallowed subjects, error transport, stale refs, duplicated input fields, and bound result identities. The one-process CLI must be invoked only after PORTAL has atomically marked the dispatch attempted; otherwise the local operator can bypass host scheduling invariants.

On Windows the executable `python` must be an actual installed interpreter, and `gh` must be authenticated with repository read access. Neither discovery nor CLI presence establishes runtime qualification. No secrets are embedded in this source.
