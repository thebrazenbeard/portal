# Vera Mono ↔ PORTAL advisory task-route handoff V1

This is one small cross-repository contract, not another scheduler, task store,
delegation engine, or permission system. It is **advisory-only**.

Canonical durable task, lifecycle, permissions, dependencies, checkpoints, and
closeout remain in Vera Mono's TaskExecutionLedger/QualifiedVeraRuntime.
PORTAL owns its existing scheduling and read-only route assessment through
PortalRouteRequest, PortalRouteAdvertisement and resolve_portal_route.

## Request

VERA_PORTAL_TASK_ROUTE_REQUEST_V1 has exactly:
- schema (above);
- task_id (existing open ledger task);
- task_packet_digest (the real ledger packet's SHA-256);
- task_journal_head (the current exact journal head SHA-256);
- subject (actual ledger packet subject);
- route: node_id, target_kind, target_id, required_capabilities (sorted unique IDs);
- requested_effect: **NO_PROTECTED_EFFECT** (fixed);
- request_digest: SHA-256 of all other keys as compact UTF-8 JSON, keys
  sorted lexicographically. This restricted v1 field set uses ASCII field
  names so the digest matches Vera Mono canonical JSON for its key order.

A request can be reconstructed only from an actual open Vera Mono ledger
task, never from a PORTAL caller's claimed packet or a chat transcript.

## Assessment

VERA_PORTAL_TASK_ROUTE_ASSESSMENT_V1 has exactly:
- schema (above);
- the same task_id, task_packet_digest, task_journal_head, request_digest;
- state: ROUTE_PROPOSED or HELD;
- route: adapter_id and route_id, only for ROUTE_PROPOSED;
- reason: NO_QUALIFIED_ADVISORY_ROUTE only for HELD; otherwise null;
- assessment_digest: SHA-256 of all other assessment keys (same canonicalization).

PORTAL's assessment uses the *existing* resolver with subject_kind VERA_TASK,
required_effect NO_PROTECTED_EFFECT, and exact node/target/capabilities.
Unavailable, unattached, stale, mismatched or unauthorized advertisements
produce HELD. Duplicated advertisements are an error, not a silent HOLD.

Vera reopens the current task, recomputes both digests and rejects mismatched
identities and **stale journal heads**. Verification returns only a dict;
it does not checkpoint, close, delegate, authenticate the PORTAL signer,
mint a lifecycle permit or authorize any external effect. The digest detects
accidental/tampered bytes but is NOT a cryptographic origin signature.
PORTAL route advertisement 'current' flags need fresh host evidence before
any protected effect. This V1 intentionally does not dispatch work.

No sibling-repository runtime import in either package. Cross-repository
behavior is exercised by tests with two independent exact source trees.
The two independent implementations must be kept in contract sync.

## Next integration frontier

Host-verified route currentness -> signed/provenance-bound assessment ->
qualified dispatch via the existing PORTAL Project Runner ->
return of durable execution receipt -> Vera task/dependency reconciliation ->
restart/ambiguous-outcome test, with the original authorization/fence held
at every step. Do not infer installation, runtime selection, behavioral success,
or protected-effect rights from these source tests.
