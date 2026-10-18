# Mono / Runner producer source contract proposal

Disposition: **source repair and executable negative conformance; runtime admission HOLD**.
Stacked on diagnostic #57 `f053fe916cc2a9965e134533cb0b5c48b86c8699`.
PORTAL released this packet in #57 comments 6250999506 and 6251075816.
A1 #55 `88026ea85c1ebc480396b582cf473df5a30e031e` remains unchanged.

## Implemented repairs

Direct `SqliteDispatchAdmissionStore.admit(now=100)` hashed a JSON integer while
SQLite REAL readback returned `100.0`, invalidating the original attempt digest.
Claim-only re-fencing had the same defect. `_claim_clock` now validates and
normalizes both writer inputs before hashing, storage or `BEGIN IMMEDIATE`.
Boolean/nonfinite values, integers that cannot be represented exactly, and
overflowing/nonadvancing expiry are rejected before state mutation. A clock is
still caller-supplied data, not host authentication. Schema and read-time digest
verification are unchanged; historical incompatible rows still fail closed.

The #57 test-artifact reader now returns structured HOLD/NO_EFFECT for malformed
intake/request/assessment mappings and invalid database path values. Its receipt
never authenticates a producer or authorizes dispatch. Closed immutable SQLite
fixtures remain historical observations; WAL/SHM and live-writer qualification
limits remain in force.

## Existing mechanism and the missing owner boundary

Mono `QualifiedVeraRuntime.delegate_task_work` creates a real canonical
`TaskDelegationRef`. The actual validator checks active task/delegation, exact
repository/ref/subject/assignee/binding event and a supplied actor. This does not
authenticate an actor label or copied state root. An active reference can survive
a same-task checkpoint; currentness additionally requires the latest TASK event
digest. The immutable opener, latest TASK head and verified GLOBAL chain tip are
different fields. Unrelated Task B must not invalidate Task A.

The existing Runner `claim_bound_plan_subject` producer does not accept a Mono
runtime or delegation reference and does not include their immutable association
before WorkUnit fingerprinting. Its public clock entry already normalizes time;
the repair above fixes the native direct admission/recovery entrypoints.

`tests/proposals/mono_runner_producer_contract.py` is an executable proposal,
outside production packages. Under Mono's existing action lock it validates an
actual delegation and TASK head, derives original packet/lifecycle/opener/source
and selected target association, detaches nested input, then computes the native
WorkUnit fingerprint. Revalidation rejects changed work or a committed same-task
checkpoint, including in a cold process. Unrelated B preserves A's fingerprint.
It never initializes/adopts/admits the proposed WorkUnit. Every receipt remains
HOLD/NO_EFFECT with `producer_authenticated=false`.

The exact source result is a producer proposal, not an approved ABI. An owner
must bind the original verified plan/wave/registry, trusted issuer and selected
canonical/Runner domain in the existing producer before admission. A task lock
does not atomically commit another database. Lock ordering or an existing intent
and reconciliation protocol must address death between stores without new task,
claim or dispatch identifiers. Public rehashing, a path, actor string or copied
database cannot supply those missing contracts. No alternative ledger/service is
introduced here.

## Executable acceptance and explicit unresolved REDs

There are 116 added normally collected cases: 51 clock-domain, 14 native
concurrency/recovery, 17 malformed-evidence, 20 delegation/adversarial, 6 actual
qualified-runtime cold composition, and 8 producer-proposal cases. Of the 20
adversarial cases, 15 exercise actual Mono, 2 actual Runner fingerprinting and
3 are explicitly conceptual public-hash controls. No conceptual control is
reported as production-component qualification.

Real processes exercise uncommitted rollback, death after claim commit, original
holder replay, expired claim-only re-fencing, concurrent recovery, superseded
fences/generations and cold source reservation with unknown outcome. Fixture
claims use visibly offline source responses; they are not genuine selected host
or producer authentication. No provider/model/backend dispatch, memory admission
or canonical task closeout occurs in the new packet.

Two stronger-contract assertions remain **RED**, separately executable in
`tests/diagnostics/runner_claim_concurrency_probe.py`:

- Different fingerprints sharing one collision key both acquire native leases in
  one database. The native lease key is the work fingerprint.
- Independent roots have independent leases, even with the same claimed domain.

The default regression wrapper asserts the actual local API behavior. It does
not export those two unsatisfied stronger assertions as established guarantees.
They are neither XFAILed nor changed to pretend shared exclusion. Run them
explicitly; the recorded outcome is 0 PASS / 2 FAIL / 12 deselected. A positive
cross-host claim path remains HOLD until a genuine owner-controlled collision
domain and producer correlation exist.

## Reproduction and evidence

`docs/evidence/MONO_RUNNER_NATIVE_EXECUTION_20261018.json` records actual parent
and four native task IDs, source heads, commands, raw-byte hashes and JUnit/log
hashes. Child findings were integrated by the parent and rerun; children are not
independent VN1 review. Session capacity was four including the parent, so D
started only after A returned. No second top-level worker or new checkout.

Lappy Windows11 build26300, CPython3.12.10, SQLite3.49.1: full repository
**939 PASS / 1 FAIL / 1 SKIP**. The unchanged wider failure is
`test_session_run_persists_non_secret_resume_envelope`, exit2 GitHub discovery,
previously reproduced on #52. The skipped opt-in live read-only GitHub test was
separately executed: **1 PASS**, no host dispatch. The parent expanded focused
run passed 90 cases; the full run additionally included the original #57 packet
and new actual-component cold/delegation tests. Do not sum overlapping runs.

VN1's latest return is Bus #368 comment 6251227031: physical independent
execution is BLOCKED because VERA-NODE-1 is disconnected. Prior A1/unchanged
resident evidence is historical and does not certify this patch. Request exact
new-head independent review from VN1; PORTAL, Integration and Training retain
owner review. No source PASS qualifies installation, a model, guest, trust,
production claim, protected effect or system acceptance.
