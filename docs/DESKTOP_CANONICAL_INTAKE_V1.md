# Desktop canonical Mono intake (source candidate)

Desktop human input now opens the existing Mono task before any operational
cognition, discovery, model transport or result acceptance. This source slice
has **no admitted provider path**: every accepted message returns canonical
`HELD` with explicit recovery status. It never invokes the cognition engine,
including for advertised `auto_admissible` or synthetic routes. Autonomous
operational engine behavior is outside this increment and is unchanged.

## Owner contracts and source dependency

- PORTAL locator policy: [Bus #368 / 6233757408](https://github.com/thebrazenbeard/chat-communication-bus/issues/368#issuecomment-6233757408).
- Integration ABI ACK: [Bus #368 / 6234499327](https://github.com/thebrazenbeard/chat-communication-bus/issues/368#issuecomment-6234499327),
  [Mono #77 / 6234501080](https://github.com/thebrazenbeard/vera-mono/pull/77#issuecomment-6234501080).
- Tested canonical ABI: Mono #77 `89ba1d18da78e7e38f015cf8d8ef4f528aae9df8`,
  `local_conversation.py` blob `d243d2efd01777655e511d91ea816fd804488ecf`.
  No Mono, VeraOS or Pre-Active source changes are part of this increment.
- PORTAL base: #52 `b16413fad12d33a9f9b64d6b1602490f4dede327`.

`DesktopRuntimeCommandHandler` receives the actual `QualifiedVeraRuntime`
explicitly from the host and uses its `tasks` owner. It invokes existing
`open_message(tasks, conversation_id, request_id, exact_text)`, then reads
existing `recover_message`/`recover_conversation` evidence. Missing composition
fails closed. No new task identifier scheme, task database, provider gate or
canonical memory is introduced. `source_store` is the host's existing
Pre-Active store for read-only reconciliation; a missing source needed for
recovery holds rather than dispatching or sealing results.

## Client and transport

The user explicitly selects **Begin new canonical conversation**. The UI
creates `desktop-<uuid4 hex>` and stores only a schema and selected locator in
`<runtime_root>/bridge/ui/active-conversation.json`. It never imports legacy
activity. Startup reads a valid selector without creating one. Missing state
requires explicit new selection; corrupt, duplicate-field or conflicting
state blocks. Atomic file replacement and Mono's existing `PortableFileLock`
serialize selector changes with an expected-current-selector check. The lock
contains only lock metadata, not conversation/task state or authority.

`message_id` is exactly the existing `request_id`. The handler validates Mono's
alphanumeric-first, at-most-96-character locator grammar without trimming.
The file transport additionally keeps its filename-safe grammar (no colon in
request filenames); normal UUID request IDs fit both. No legacy ID is rewritten.
Literal task text is unchanged, or explicitly rejected under Mono's policy
(non-string, empty, surrounding whitespace, more than 2048 characters).

Canonical bridge claims and responses are retained as transport evidence.
An identical replay checks the original message and returns the same task;
different conversation/text with a retained transport ID is rejected.
Claimed missing or incomplete responses remain unknown, never republished or
retried under another ID. On UI restart the original pending ID is recovered
from existing transport evidence, not a new message database. Multiple or
unreadable pending claims block. A fresh host encountering an old claim
reconciles the canonical intake but returns `OUTCOME_UNKNOWN_NO_REDISPATCH`.

Cached responses are historical and explicitly return `task_head_current=false`.
Response reads can race Windows file sharing/access restrictions. Only the
read is retried, using the original request deadline; later success must still
pass the original source/response checks. Persistent denial, other I/O failure,
or incomplete response yields UNKNOWN without a new ID or dispatch. Once a
response has been observed, its disappearance cannot become a safe pre-claim
timeout: original transport evidence and the UI pending ID remain for recovery.
Generic host errors also remain UNKNOWN: the host can catch an exception after
Mono opens a task, so `ok=false` cannot prove pre-dispatch rejection. Live and
cold clients retain the original ID; cold pending retirement requires explicit
boolean `ok=true` with the matching request and held source response.
Use `desktop_conversation_history` for fresh Mono source/head evidence. It is
read-only, labels input untrusted, and never promotes observed model text to
dialogue, truth, task completion or memory. An operational `COMPLETED` row is
labelled `LEGACY_OPERATIONAL_EVIDENCE_ONLY` and cannot supply canonical results.
The bridge rejects fabricated completion/admission responses. These local JSON
files remain untrusted and do not authenticate a user or provider.

## RED evidence and contract-driven test adjustments

The original five A1 failures, original test bytes, patch, log, JUnit and hashes
are preserved in the A1 output packet. The unchanged five cases were rerun RED
before production edits. Their names and provenance goals remain in
`test_desktop_mono_canonical_intake.py`.

The working tests now inject the real canonical runtime and an explicit test
conversation. Per the accepted contract, the first case expects `HELD`, zero
discovery and no operational write, replacing the earlier `UNRESOLVED` row and
one discovery assertion. The literal-input cases still require preservation or
explicit rejection. The synthetic-result case still requires one real task,
no closeout and zero synthetic transport calls. The view-model case explicitly
selects its test locator rather than requiring an implicit identity default.
Existing IPC tests for automatic legacy cognition were updated to explicit
blocking/labelled legacy evidence; engine-only operational tests are unchanged.

## Running source checks

Use an existing Python environment with pytest, PyYAML, jsonschema and
cryptography. Put the PORTAL checkout and exact Mono `packages/*/src` on
`PYTHONPATH`. The real host-process test additionally needs pinned Pre-Active
`1f23a809d7274df506e03e2d9052525538c3bcf6/src` on that path. No environment
installation, model loading, deployment or running Desktop change is needed.

Run `python -B -m pytest -q` from PORTAL for the full source suite. The five A1
cases and canonical bridge suite explicitly skip if Mono is absent; the actual
host-process test explicitly skips if Mono/Pre-Active is absent. Those skips
are **not** canonical qualification. The A1 Lappy qualification must run with
the pinned dependencies and report no such skips. Selector corruption tests
are independent of Mono; selector creation/concurrency tests need the existing
Mono lock primitive.

The actual host test opens two separate processes across a cold restart, uses
real bridge claims and canonical state, forbids route/network boundaries, and
checks SQLite release on Windows. Mutex checks cover the existing Windows
local-session root boundary, including different package source manifests.
They do not prove cross-session or cross-host whole-root ownership.

The cold-host test records each actual interpreter PID in a unique disposable
startup witness, then requires its ACTIVE bridge heartbeat. Windows venv
`python.exe` can launch a base interpreter whose PID differs from `Popen.pid`;
the wrapper PID is not a valid host readiness identity. The test retains the
same venv environment, 15-second bound and all canonical/restart assertions.
No production PID, task, source-currentness or mutex policy is changed.

This candidate does not migrate Desktop identity roots, install this source,
admit a provider, authenticate client locators, qualify a graphical installed
runtime, or establish VeraOS package/guest conformance. Independent Integration,
Training and VN1 review precedes any promotion.
