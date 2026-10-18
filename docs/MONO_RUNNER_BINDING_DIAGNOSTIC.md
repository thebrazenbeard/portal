# Mono / Runner binding: negative source diagnostic

This packet implements only PORTAL's diagnostic release in PR55
[6246008473](https://github.com/thebrazenbeard/portal/pull/55#issuecomment-6246008473).
Base is PORTAL56 `6183aa7e8ded004054f185f62a0a40a5d55ffb10`. It changes no
production package, API, SQLite schema, provider or execution route. A1 remains
at `88026ea85c1ebc480396b582cf473df5a30e031e`, source/disposable-host accepted
by PORTAL, independently tested by VN1; installed Desktop is unchanged.

`tests/diagnostics/mono_runner_binding_probe.py` is a test artifact, not an
installed verifier, new task authority, claim service or authenticated receipt.
Its only verdict is **HOLD / NO_EFFECT**. A passing test means negative evidence
is explained without mutation; it cannot mean a usable cross-host route exists.

## Real source boundaries exercised

The integration test uses actual Mono `TaskExecutionLedger` and `open_message`,
PORTAL route assessment and Mono advisory verification, and Runner's existing
`claim_bound_plan_subject` / `initialize_root` / `admit` fixture producer. The
producer creates one durable CLAIMED work/lease/attempt using an explicitly
offline transport that returns `a` repeated40 times. This is not observed target
repository currentness, a live lease or an authenticated owner. Production
roots and installed Desktop are never opened by these tests.

After fixture setup, inspection opens the existing Runner SQLite file read-only.
No store constructor, admission, recovery, renewal, refencing, migration or
dispatch is called. The existing native lease guard, immutable WorkUnit digest,
semantic fingerprint and attempt digest are checked. Work/budget/attempt
generations, repository/ref/head, lineage, holder/fence/expiry are compared.
Missing operational response stays unknown; an operational success cannot close
the canonical task or admit memory.

The current producer payload has no approved immutable Mono task/source/head
association. Merely copying a task ID into caller metadata, a WorkUnit or a
receipt does not add that ABI. Even structurally valid evidence therefore emits
`MONO_WORKUNIT_PRODUCER_ABI_MISSING` and `CLAIM_DOMAIN_NOT_AUTHENTICATED`.
Root-path equality is only a fixture check, never host identity. Re-pinning a
copied database to a caller-declared root still cannot authenticate it.

The diagnostic keeps `task_head` (latest task event), `opened_head` (original
opener), and `global_head` (whole journal) distinct. Existing route-request
`task_journal_head` remains its existing global-journal ABI; it is not silently
renamed. Unrelated B may stale global advisory while A's task head stays intact.
The Integration-owned A/B/A history/ACK correction in Mono77/OS38 is separate.
VeraOS omitted-conversation compatibility remains unchanged.

## Closed-fixture SQLite ceiling

On Windows, `mode=ro` against a WAL-mode database can create/leave WAL and SHM
sidecars. The cold test exposed this in the first diagnostic implementation.
The test artifact refuses any existing sidecar and uses
`mode=ro&immutable=1` only for a closed, disposable fixture, checking file bytes
and SQL changes. It performs no network observation inside a SQLite transaction.
This is **not** a live writer/currentness protocol. Concurrent writers, expiry
at another host, cross-root collision ownership and authenticated source
admission remain unqualified. Every report remains HOLD regardless of hashes.

The cold test starts a distinct Python process using the original task, request,
literal, claim and fixture-clock values. It checks identical diagnostic receipt
hash, one task opener, one admission and no result; then interrupts a reader
before delivery and confirms the same evidence survives. Mono is reopened via
its existing constructor only in its disposable fixture (which performs its
existing schema inspection); the Runner reader never constructs a store.

## Verification and reproduction

Set PYTHONPATH to this PORTAL checkout and explicitly selected Mono package
`src` roots; no install or sibling runtime dependency is added. The qualified
run uses Mono77 `a351d36274595cde26bc60df4c44c3b83a6d1f0a` with runtime
`local_conversation.py` blob `fdc9b3d8398b43d620d487664931c0c4ebc18d1e`.
Without Mono the integration file explicitly skips; that is not qualification.

```powershell
python -B -m pytest tests/integration/test_mono_runner_binding_negative.py -q -p no:cacheprovider
python -B -m pytest tests -q -p no:cacheprovider
```

The exact executed command/environment transcript, logs, JUnit, source bytes,
process IDs and artifact hashes are returned with the implementation receipt.
Lappy Windows11 build26300 / CPython3.12.10: focused41PASS/0FAIL/0SKIP;
whole PORTAL56-based suite823PASS/1FAIL/1SKIP/0ERROR. The failure is the known
unchanged #52 GitHub-discovery CLI baseline; the skip is the existing opt-in
live GitHub observer. Running that observer explicitly then passed1/1 with no
host dispatch. These overlapping scopes are not one combined zero-skip run.
Full-suite pytest9.0.3; focused pytest9.1.1. Actual cold reader PID20876 differs
from parent4560; receipt `99dfbc5552549acf63167dfffbfa623e4d8602cf469fbe8ac28bfec8294f6c6b`.
The first15 RED failures reached actual task/claim setup and showed the missing
composed diagnostic; they do not claim to demonstrate an execution bypass.
An earlier route-fixture argument error is preserved separately as harness
evidence. Subsequent cold-reader failures led to the sidecar correction.

VN1 supplied independent A1/actual cold-host findings and the public-digest
forged-advice falsifier used here; pre-edit and mid-implementation challenges
were sent through Bus368. Independent review of this new exact commit remains
required. Codex's own tests are not a VN1 verdict or a joint-code certification.

## Owner decisions still required

Production binding remains withheld. PORTAL/Integration must select the actual
existing producer and immutable Mono correlation, authentic claim domain and
collision ownership, read-only currentness and receipt/replay ABI, and explicit
qualification ceiling. No positive branch, lease repair, provider or effect is
invented to make these source tests pass. Training generation/session issuer
spoofing, target ELF/import/guest qualification, upstream VirtIO and architecture
semantic acceptance remain separately owned open review surfaces.

PORTAL's mid-implementation [6246667909](https://github.com/thebrazenbeard/chat-communication-bus/issues/368#issuecomment-6246667909)
confirmed this historical closed-snapshot ceiling and preserved the missing
producer/collision-domain HOLD. It did not supply independent VN1 acceptance.
