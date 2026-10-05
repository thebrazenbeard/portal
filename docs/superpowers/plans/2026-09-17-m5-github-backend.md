# Project Runner M5 — Real GitHub Backend Implementation Plan

Date: 2026-09-17
Branch: `vera/m5-github-backend`
Base: `main@c5926dbccd1d597a722db769a6bc0b508bbd8ca8`

## Goal

Add the first real GitHub execution backend while preserving the M4 safety model.

M5 introduces a live GitHub REST route, but execution capability and target authority remain independent gates. A backend that can technically call GitHub does not gain permission to mutate a repository merely because credentials exist.

## Hard invariants

- Route capability and target authority are separate and both are required.
- Target authority is explicit per repository, operation, ref, and path scope.
- Child work cannot expand parent capability or target authority.
- Every mutation with a mutable predecessor binds an expected current head/blob and fails closed on mismatch.
- Mutation success requires post-write readback.
- Persistent lineage budgets cannot reset across process/workflow boundaries.
- Persistent leases preserve atomic claim, expiry/reclaim, and monotonic fencing across restarts.
- A stale fence cannot commit completion.
- M4 collision, dedup, recursion, and exact-subject post-work verification remain in force.
- The live CI smoke route is read-only. No M5 CI job writes downstream repositories.
- Registered Custom GPTs remain non-executable until independently demonstrated.

## Task 1 — GitHub request + authority contracts

Add typed:
- GitHub operation enum;
- GitHub request payload;
- backend route capability set;
- target authority grant;
- deterministic request identity.

Tests:
- route capability alone cannot authorize target action;
- target grant alone cannot compensate for missing route capability;
- wrong repository/ref/path fails closed;
- grant cannot widen through work-unit payload.

## Task 2 — Work-unit payload binding

Extend WorkUnit with a canonical operation payload included in the semantic fingerprint and JSON Schema.

Tests:
- equivalent payload ordering hashes identically;
- material request changes alter fingerprint;
- work ID/provenance IDs remain outside semantic identity.

## Task 3 — GitHub REST transport

Implement a stdlib GitHub REST transport for:
- read ref/head;
- read file metadata/content;
- create branch;
- create/update UTF-8 file with expected predecessor;
- post-write ref/file readback.

Token comes only from explicit runtime configuration/environment. No credentials are committed.

Tests use a fake transport. Network mutation is not used in unit tests.

## Task 4 — GitHub backend gate

GitHubBackend:
1. parse request from work payload;
2. check backend route capability;
3. check explicit target authority;
4. read exact current subject;
5. enforce expected-head/blob precondition;
6. perform operation;
7. read back exact result;
8. return evidence.

Authority rejection is distinct from transport failure and precondition failure.

## Task 5 — Persistent state

Add SQLite-backed durable stores for:
- lineage budgets with generation/CAS;
- leases with atomic claim/reclaim and monotonic fencing.

Tests:
- reopen database preserves state;
- concurrent/stale generation update fails;
- expired lease reclaim increments fence after restart;
- stale fence cannot complete after reclaim.

## Task 6 — Live read-only GitHub smoke

Add a CLI command and CI step that uses the actual GitHub REST transport only to read the workflow repository/ref.

CI permissions remain `contents: read`.

The smoke must verify the observed head matches the expected workflow subject where the event semantics make that comparison valid.

## Task 7 — End-to-end guarded backend tests

Fake transport tests cover:
- exact-head mismatch blocks mutation;
- missing target grant blocks mutation;
- path/ref scope violation blocks mutation;
- authorized create-branch/write route produces exact readback evidence;
- post-write drift/readback mismatch fails;
- M4 verification still independently rechecks exact subject before COMPLETE.

## Completion gate

M5 is complete only when:
1. full repository tests pass on exact feature head;
2. live read-only GitHub smoke passes;
3. registry validation passes;
4. branch remains a clean descendant of canonical M4;
5. no CI or test path mutates another repository;
6. docs accurately state the M5 authority/effect ceiling.
