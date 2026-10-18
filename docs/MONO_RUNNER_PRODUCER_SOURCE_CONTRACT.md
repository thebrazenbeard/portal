# Mono / Runner producer source contract proposal

Disposition: **source repair and executable negative conformance; runtime admission HOLD**.
Stacked on diagnostic #57 `f053fe916cc2a9965e134533cb0b5c48b86c8699`.
PORTAL released this packet in #57 comments 6250999506 and 6251075816.
A1 #55 `88026ea85c1ebc480396b582cf473df5a30e031e` remains unchanged.

## Durable local collision increment

The successor collision packet is based on #58
`f5cded4d883be09ed45a246806f9f65cd20cd779`. It implements the previously missing
**same SQLite root** exclusion in existing Runner admission. The historical
same-root RED below is now a passing regression; the independent-root assertion
remains explicit RED. No Mono producer authentication is added.

`collision_reservations` lives in the existing Runner database. Each exact key
has a current work fingerprint/holder/work fence and a monotonically increasing
local generation. Release retains a generation tombstone. All keys are reserved
in the same `BEGIN IMMEDIATE` transaction as the lease, attempt journal, budget
and work generation CAS. A competing or interrupted admission leaves no partial
keys, lease, attempt, budget consumption or work status change.

Keys are literal strings. Empty key sets are supported and mean no declared
collision keys. Empty strings, duplicates, edge whitespace, Unicode control and
surrogate characters reject; no trim, case folding, prefix hierarchy or Unicode
normalization is inferred. Key ordering does not alter exclusion. Producers
remain responsible for declaring the correct resource keys.

The shared local fence validator covers durable admission/recovery,
verification/reconciliation, recursive work transitions and generic SQLite lease
operations when actual immutable work is present. Generic fingerprint-only
leases with no durable work do not establish a collision guarantee. Generic
claim/release/complete cannot bypass an unresolved durable attempt. Old active
or released lease shapes without proven conclusive outcome do not acquire
ownership through a silent backfill. This is source compatibility handling,
not authorization to migrate a production root.

Expiry alone never releases another work's keys. Existing exact claim-only
recovery may re-fence structurally execution-forbidden work atomically, preserving
the original identity and retaining the keys. Public `NO_EFFECT_CONFIRMED`
declarations are rejected: no authenticated independent no-effect verifier is
selected by this interface, including for claim-only payloads. Reason, evidence,
actor strings and recomputed public hashes do not supply one. Historical labels
cannot hide an older unknown attempt or free missing legacy reservations.
Unknown outcomes retain ownership and remain visible in recovery reports.
Admitted work conservatively retains its collision keys even after a terminal
journal classification: no independent authenticated release verifier is
selected. This liveness limit is explicit; a future owner-approved release
contract is needed before those keys can be reused by another work.

The positive local ownership-transfer control cancels a never-dispatched
recursive reservation through the existing fenced `SUPERSEDED` terminal CAS;
there is no execution attempt or asserted host outcome. It then admits a
different fingerprint with an increased collision generation. This does not
authenticate an external no-effect result. Existing terminal verification remains
its own qualified boundary, not replaced by a caller's reconciliation string.
A prior owner cannot heartbeat, release, complete or advance a successor even
when the two fingerprints have equal work fencing tokens. Local collision
generation remains monotonic across those different fingerprints.

`execute_admitted` checks current local lease/collision ownership, work
generation and absence of an already observed result before its backend call.
This preflight is not an atomic fence on an external effect; that downstream
boundary remains unqualified. The packet never executes an actual backend.

Real Mono task/delegation tests continue to reject stale Task A heads while
allowing unrelated Task B global changes. Actor strings, copied roots and public
hashes never authenticate the missing producer. Independent SQLite files can
still each commit a local owner, even with matching domain strings; higher
composition remains HOLD/NO_EFFECT. No cross-store atomicity is claimed.

New execution evidence is recorded separately in
`docs/evidence/RUNNER_COLLISION_FENCE_NATIVE_EXECUTION_20261018.json`; the #58
manifest and its original failures remain historical evidence.

PORTAL explicitly tightened the reconciliation contract during this packet
after independently executing the forged-string release on unchanged #58.
Three older recovery tests that assumed those strings authorized retry failed
under the new policy (7 PASS / 3 FAIL, preserved). Their assertions now require
unchanged state, unresolved recovery and blocked retry; a non-releasing
observation still checks conclusion immutability. This is an intentional owner
contract correction, not a relaxed assertion or an unrelated baseline failure.

Final successor verification on Lappy Windows11 build26300, CPython3.12.10,
SQLite3.49.1, pytest9.0.3: **1,016 PASS / 1 pre-existing CLI discovery FAIL /
1 opt-in SKIP** (1,018 cases, 156.63s). There are 76 added cases: B27 admission,
C9 cold process, D40 hostile/actual Mono. B also passed 67 existing operator,
queue and storage controls (94 focused total); C and D reran their unchanged
tests against the same final source. Earlier strict-source full run
995 PASS / 17 FAIL / 1 SKIP is preserved: fourteen actual operator compatibility
regressions were repaired in source, recognizing its existing exact
CLAIMED-to-RUNNING generation CAS without admitting arbitrary later RUNNING
states or forged budget/attempt identities. Two reporting/error-order test
updates require stronger history visibility and full unchanged-state rejection.

The original same-root stronger assertion is unchanged and passes; the original
independent-root assertion is unchanged and fails. Their explicit combined run
is 1 PASS / 1 FAIL / 12 deselected. The default suite does not silently XFAIL
the remaining global-contract gap.

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

The exact source result is a producer proposal, not an approved ABI. A real cold
process can commit a direct canonical checkpoint while another process holds
`action_lock`: that separate portable lock does not fence every task writer.
The new negative reproduces this and then rejects both stale proposal
revalidation and stale native task CAS. No lock-only admission is qualified.
An owner must bind the original verified plan/wave/registry, trusted issuer and selected
canonical/Runner domain in the existing producer before admission. A task lock
does not atomically commit another database. Lock ordering or an existing intent
and reconciliation protocol must address death between stores without new task,
claim or dispatch identifiers. Public rehashing, a path, actor string or copied
database cannot supply those missing contracts. No alternative ledger/service is
introduced here.

## Executable acceptance and explicit unresolved REDs

There are 117 added normally collected cases: 51 clock-domain, 14 native
concurrency/recovery, 17 malformed-evidence, 20 delegation/adversarial, 6 actual
qualified-runtime cold composition, and 9 producer-proposal cases. Of the 20
adversarial cases, 15 exercise actual Mono, 2 actual Runner fingerprinting and
3 are explicitly conceptual public-hash controls. No conceptual control is
reported as production-component qualification.

Real processes exercise uncommitted rollback, death after claim commit, original
holder replay, expired claim-only re-fencing, concurrent recovery, superseded
fences/generations and cold source reservation with unknown outcome. Fixture
claims use visibly offline source responses; they are not genuine selected host
or producer authentication. No provider/model/backend dispatch, memory admission
or canonical task closeout occurs in the new packet.

At the historical #58 cut, two stronger-contract assertions were **RED**, separately executable in
`tests/diagnostics/runner_claim_concurrency_probe.py`:

- Different fingerprints sharing one collision key both acquire native leases in
  one database. The native lease key is the work fingerprint.
- Independent roots have independent leases, even with the same claimed domain.

The default regression wrapper asserts the actual local API behavior. It does
not export those two unsatisfied stronger assertions as established guarantees.
They were neither XFAILed nor changed to pretend shared exclusion. The historical
outcome was 0 PASS / 2 FAIL / 12 deselected. The successor local collision
increment above changes only the same-root guarantee. A positive
cross-host claim path remains HOLD until a genuine owner-controlled collision
domain and producer correlation exist.

## Reproduction and evidence

`docs/evidence/MONO_RUNNER_NATIVE_EXECUTION_20261018.json` records actual parent
and four native task IDs, source heads, commands, raw-byte hashes and JUnit/log
hashes. Child findings were integrated by the parent and rerun; children are not
independent VN1 review. Session capacity was four including the parent, so D
started only after A returned. No second top-level worker or new checkout.

Lappy Windows11 build26300, CPython3.12.10, SQLite3.49.1: full repository
**940 PASS / 1 FAIL / 1 SKIP**. The unchanged wider failure is
`test_session_run_persists_non_secret_resume_envelope`, exit2 GitHub discovery,
previously reproduced on #52. The skipped opt-in live read-only GitHub test was
separately executed: **1 PASS**, no host dispatch. The parent expanded focused
run passed 90 cases; the final full run additionally included the original #57
packet, actual-component cold/delegation tests and the late peer-reported cold
writer/action-lock negative (separate nine-case proposal rerun also passed).
The prior full run was 939 PASS / 1 FAIL / 1 SKIP before that extra case.
Do not sum overlapping runs.

VN1's latest return is Bus #368 comment 6251227031: physical independent
execution is BLOCKED because VERA-NODE-1 is disconnected. Prior A1/unchanged
resident evidence is historical and does not certify this patch. Request exact
new-head independent review from VN1; PORTAL, Integration and Training retain
owner review. No source PASS qualifies installation, a model, guest, trust,
production claim, protected effect or system acceptance.
