# Installed-wheel reference-worker restart qualification

`scripts/installed_worker_restart_qualification.py` is a bounded, read-only
test for the proposed Portal/Project Runner package separation. It is not a
service installer, private Bus client, model worker, or deployment gate.

## Source pins tested on 2026-11-06

- Portal candidate Draft #122: `963d4b31eebb45faec3c4bd41a92935e36a0c57c`
- Project Runner candidate Draft #97: `4c3e2c2ac51778a1909ad82e24dbaea491f3b7c9`
- Target public Project Runner `main`: `44da26e02c39591118bf1fdfe7ec01bd1103fc66`

The companion scripts are intentionally proposed against Portal `main` while
**depending on the unmerged candidate wheels**. Main's current `runner`
namespace collision is not repaired by these scripts; do not install main
Portal and main Project Runner together. No production host modification is
part of this proof.

## Reproducible execution

Use a throwaway Python 3.12+ virtual environment with both exact candidate
wheels installed using `pip install --no-index --no-deps`. Make the existing
Portal dependencies `PyYAML>=6,<7` and `jsonschema>=4.23,<5` available without
mutating a global Python installation. Verify `portal.__file__` and
`runner.__file__` resolve from that environment's `site-packages`.

The deterministic offline regression uses no network:

```sh
python scripts/test_installed_worker_restart_qualification.py
```

This starts three independent Python processes per positive scenario using
one disposable SQLite file beneath the OS temporary directory, plus three
restricted local witness files (`qualification.key`, `qualification.prepare.json`,
and `qualification.consume.json`):

1. `prepare` admits exactly one public, read-only `RUNNER_ACTION_PULL` route.
2. `consume` runs the installed-wheel reference worker and records a fenced
   `SUCCEEDED` receipt. The offline test explicitly supplies a synthetic
   transport and labels it `SYNTHETIC_OFFLINE`.
3. `verify` reopens the durable database in a fresh process. It requires a
   signed preparation witness (exact route and public head) **and** a separate
   signed consume witness recording the worker's observed public ref reads and
   canonical receipt content. It independently hashes that content and
   compares the result with SQLite's exact route, receipt SHA-256 and delivery
   fencing token before checking that the terminal route cannot be claimed
   again. Absent or changed witnesses fail closed.

Negative cases reject superseded public heads, repeated state initialization,
an arbitrary/non-temp database destination, an invented terminal SQLite receipt
without a consume witness, a forged receipt digest/fencing token after a real
consume, and tampering of the consume witness. The standalone CLI requires
`qualification.sqlite3` under the OS temporary directory and refuses to reset
an existing database.

This witness is an **isolated local qualification integrity check**, not an
external signature or an adversarial custody system. The HMAC key is stored
beside the disposable database with restrictive file mode; an actor with full
same-user filesystem access could replace the key and both signed witnesses.
That actor is outside the proof's threat model. A DB-only forged row cannot
reach `REOPENED_AND_VERIFIED`. No claims of private Bus or installed host
receipt authenticity should be inferred.

For a **separate live public-read-only** probe, first refresh the target branch
SHA independently. Within the temporary environment, call the same script's
`prepare` (with `--target-head <exact 40-hex head>`), `consume` without any
fixture flag (uses public GitHub `read_ref` only), and `verify`. No GitHub token
is necessary for a public repository; no provider POST or GitHub mutation is
performed. The result is a read-only repository-frontier receipt, **not** Vera
runtime activation or cross-host orchestration.

## Actual isolated result

In a disposable Linux/Python 3.12 environment with the above exact wheels:

- Portal wheel SHA-256:
  `8e170932879b52307860143d99bf9745f92a35fbc6b324592a9df5784cfcda1c`
- Runner wheel SHA-256:
  `628df41ff6bc163f6ab7ac3a398848c101ec9aaea54c34fb097847f2a7c145f2`
- Public GitHub `read_ref` produced `SUCCEEDED` with receipt SHA-256:
  `8fe5a52866d9c9ac2f426f6cf50310889ff553eed478de115f9ece6f271c987e`
- After a separate-process reopen, same receipt SHA-256 and fencing token `1`;
  `replay_claimed=false`.
- Offline standard-library regression after hostile falsification:
  **7/7 tests passed**.

Hostile review falsifier (2026-11-06) previously created a fabricated terminal
SQLite row and received a false `REOPENED_AND_VERIFIED` result. The corrected
implementation requires both authenticated witness files, cryptographically
binds the prepared route and independently recomputes the worker's canonical
receipt from captured public read observations. The demonstrated forged-row
case now raises `RuntimeError: trusted durable witness missing`.

`evidence_class=ISOLATED_INSTALLED_WHEELS_PUBLIC_READ_ONLY` is deliberately
limited. The proof does not establish a deployed Portal service, private Bus
mailbox, authenticated model routing, reference-worker identity on Lappy, real
runtime reconciliation of unknown effects, or permission to merge/install.
