# P.O.R.T.A.L. persistent local portfolio autopilot V1

Status: **source candidate only**. This is not installed, activated, merged, or a native ChatGPT turn hook.

The control-loop entrypoint is `python -m portal.portfolio_autopilot`. It can run one cycle or continue with `--forever` and a host-owned interval, with a default maximum of four continuation attempts per hour. A disabled/unqualified worker causes polling without dispatch; an unresolved attempt stops the loop. It communicates with the existing resident P.O.R.T.A.L. bridge; no ChatGPT browser, UI, or conversational process needs to remain open.

## Authorization gates

The supervisor only calls the existing resident `desktop_portfolio` continuation operation when every gate is satisfied:

- The sole session is RUNNING, its holder equals its installed profile, and an *uncached* resident `inspect` returns the exact generation.
- Profile declares LIVE_AUTO_V1, SOURCE_ONLY, one parallel slot, and a configured proposal-process adapter.
- Exactly one execution node is enabled. Its backend is PROCESS_JSON_V1, not Codex.
- The backend inherits no additional environment variables. The **entire four-element argv** is operator-pinned via `--command-sha256`: [qualified interpreter, local_ollama_worker.py, --checkout-index or --checkout-root, absolute repository input path]. No extra flags, interpreter substitution, or input-path redirection is admitted.
- The interpreter executable bytes and local worker source bytes independently match operator-pinned `--interpreter-sha256` and `--worker-sha256` digests. These three digests must come from a separate reviewed qualification receipt; never compute them automatically from the untrusted runtime backend manifest at admission time.
- The selected checkout index file or checkout-root directory must exist and not be a symlink before any continuation.
- The resident command receives expected_generation and expected_holder. The resident rejects stale generation/holder before refreshing GitHub or admitting a new wave.
- The supervisor's SQLite journal reserves a unique attempt before IPC. An interrupted/ambiguous attempt becomes UNKNOWN and blocks all subsequent cycles until it is explicitly reconciled.
- For `LIVE_AUTO_V1` with a `PROCESS_JSON_V1` backend, the resident now requires the supervisor's three explicit digests on `run` or `continue`, reloads the command manifest, checks it did not change during parsing, and rechecks the executable, script, input path and entire argv using the loaded backend objects before admitting the wave. A missing or mismatched pin fails closed. The ordinary Codex route remains a separate case.
- These request-carried digests are **consistency constraints**, not independently authenticated trust roots. A caller able to rewrite both the backend and presented pins can still fabricate agreement. Immutable staged worker bytes / OS-enforced permissions, an independent pin-qualification receipt, and the check-to-subprocess-use window remain required for activation qualification.

No paid-model invocation, GitHub publication, deployment or installation is authorized by an autopilot tick. Model analysis remains an unreviewed source proposal behind P.O.R.T.A.L.'s existing verification and promotion boundary.

## Operational behavior

`--forever` means **persistent host-scheduled periodic attempts**, not spontaneous native ChatGPT messages. Native ChatGPT does not provide a proven universal idle event or reply-injection API to this implementation. The 60-second idle gate from Draft PR #16 is a separate candidate and cannot stand in for a real conversation-event source.

The worker accepts a prepopulated local repository index by `--checkout-root`, resolves only bounded owner/repository paths, and checks the local checkout's HEAD, origin, and cleanliness. It never clones, fetches, rewrites or pushes repositories. A separate authenticated repository intake process must qualify and maintain that index. The reader samples a small allowlist of documentation/build/test files and sends them only to loopback Ollama. All model claims remain unverified.

## Hostile review

> A timer invoking `continue` forever can burn cycles or advance session generations without actually delivering useful portfolio work.

**Accepted.** This implementation proves safe, durable *admission*, not guaranteed productive builds. Before activation, integrate a qualified repository intake/index, outcome-based backoff, source-proposal review, and a bounded work budget. Do not classify generation advancement as verified project completion.

> A second controller could read an old state and issue a competing continuation.

**Addressed:** The resident serializes the generation/holder compare-and-act; the supervisor also journals one attempt per generation. An unknown IPC outcome fails closed rather than replaying.

> An attacker could replace an apparently local process worker with paid execution.

**Partially addressed:** The one-node PROCESS_JSON_V1 manifest, empty pass-through environment, complete argument-vector SHA-256, interpreter-byte SHA-256 and local-worker-byte SHA-256 are checked at every cycle. No extra flags or alternate executable can reuse the worker's digest. The three expected hashes must originate from an independently reviewed qualification receipt, not the manifest under inspection. There remains a check-to-use window if the backend manifest or executable is modified between the supervisor's admission check and the resident host's own backend load; an immutable staged installation or host-side digest revalidation is required before production activation.

## Current cutover boundary

At the last runtime inspection, PR10b's portfolio session was STOPPED, while PR14's session was RUNNING with a disabled worker and discovery HELD. **This branch changes neither live installation.** Deploy/install/activate/cutover, changes to main, credentials/providers and paid compute remain separately authorized effects.
