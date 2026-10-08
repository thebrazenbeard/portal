# P.O.R.T.A.L. persistent local portfolio autopilot V1

Status: **source candidate only**. This is not installed, activated, merged, or a native ChatGPT turn hook.

The control-loop entrypoint is `python -m portal.portfolio_autopilot`. It can run one cycle or continue with `--forever` and a host-owned interval, with a default maximum of four continuation attempts per hour. A disabled/unqualified worker causes polling without dispatch; an unresolved attempt stops the loop. It communicates with the existing resident P.O.R.T.A.L. bridge; no ChatGPT browser, UI, or conversational process needs to remain open.

## Authorization gates

The supervisor only calls the existing resident `desktop_portfolio` continuation operation when every gate is satisfied:

- The sole session is RUNNING, its holder equals its installed profile, and an *uncached* resident `inspect` returns the exact generation.
- Profile declares LIVE_AUTO_V1, SOURCE_ONLY, one parallel slot, and a configured proposal-process adapter.
- Exactly one execution node is enabled. Its backend is PROCESS_JSON_V1, not Codex.
- The backend inherits no additional environment variables, invokes the approved local Ollama worker, and the worker's file SHA-256 matches an operator-supplied expected digest.
- The resident command receives expected_generation and expected_holder. The resident rejects stale generation/holder before refreshing GitHub or admitting a new wave.
- The supervisor's SQLite journal reserves a unique attempt before IPC. An interrupted/ambiguous attempt becomes UNKNOWN and blocks all subsequent cycles until it is explicitly reconciled.

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

**Partially addressed:** The one-node PROCESS_JSON_V1 manifest, empty pass-through environment and pinned local-worker digest are checked at every cycle. The local interpreter and host model service are still trust dependencies and require installation qualification.

## Current cutover boundary

At the last runtime inspection, PR10b's portfolio session was STOPPED, while PR14's session was RUNNING with a disabled worker and discovery HELD. **This branch changes neither live installation.** Deploy/install/activate/cutover, changes to main, credentials/providers and paid compute remain separately authorized effects.
