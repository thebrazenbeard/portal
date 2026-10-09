# P.O.R.T.A.L. desktop selected-repository proposals — source candidate

## Problem reproduced on Lappy, 2026-10-09
The installed PR22 desktop displays a single held Firesafe item, even though
the user's authenticated GitHub inventory contains many owned repositories,
including private and archived ones. The only visible Run path requires explicit
worker SHA-256 pins and its resident command session is STOPPED at
generation 2. The unattended controller latched a durable HALT when it
observed the stopped session. Displaying all repositories did not itself
supply an add/queue/run operation.

## Source-only repair
- Desktop **All repositories** shows authenticated owner inventory, including
  private and archived memberships, separately from the resident work wave.
  The installed resident does not implement the new inventory command; this
  preview uses the existing authenticated GitHub connection for a read-only
  UI query without sending unsupported IPC to PR22.
- **Queue selected** validates exact authenticated owner membership, rejects
  archived repositories and malformed identifiers, verifies the default
  GitHub commit ref and persists a uniquely identified source-head job in
  SQLite. Duplicate exact-head jobs are rejected.
- **Run next queued (local Ollama)** atomically claims at most one job across
  separate UI processes. It uses the existing authenticated GitHub CLI for
  a shallow isolated checkout, verifies its local head equals the expected
  GitHub head and re-reads the remote default ref, consumes only whitelisted
  files, and calls local Ollama with `vera-local:latest`. A validated local
  source-only review is written to an immutable job artifact and marked
  AWAITING_REVIEW. All source worktrees remain clean; no remote write,
  branch creation, merge, publication, or protected effect is possible
  through this job path.
- **View proposal** reads the stored artifact only within the isolated job
  root and checks the expected repository/head before presenting it.
- Unresolved/crashed CLAIMED jobs and explicit HALTED jobs are not
  automatically retried; a single SQLite claim gate is maintained even
  across multiple preview windows.
- The resident Run/Continue route remains distinct. It still requires
  an operator-selected pin manifest; stopped/held sessions cannot be replayed
  from the preview.
- UI: slimmer diagnostics in a separate tab, compact live-status summary,
  a scrollable repository list, and explicit labeling of Ollama output as a
  local model rather than qualifying it as Vera merely because it is routed
  through the resident.

## Evidence
- A real authenticated UI inventory probe enumerated owned repositories,
  including private and archived entries; detailed membership stays local.
- Real end-to-end source-only selected repo job:
  `thebrazenbeard/portal` exact head
  `7d00d8bf3b48c0676b2278359838ee563c66c67f`;
  queue job `84b5f7ca70f3428b97d6bff19e5da12d`;
  shallow checkout clean, rechecked head, local Ollama review produced,
  SQLite state AWAITING_REVIEW; second claim returned no runnable job.
  Artifact:
  `%LOCALAPPDATA%\P.O.R.T.A.L\isolated-source-proposals\jobs\<job-id>\proposal.json`.
- Initial local full regression cut: 990 passed, 2 skipped.
  Additional concurrency and viewer regression tests were then added;
  use the latest exact-head suite receipt before final qualification.

## Claim ceiling / hostile review
> HOSTILE REVIEWER: A separate local queue is not the installed resident's
> authenticated multi-repo dispatcher. It may provide a working user action
> but does not solve fully unattended multi-lane execution or end-to-end
> behavioral qualification for every repository.
>
> **ACCEPTED.** The UI now offers a genuine bounded local proposal workflow
> for selected repositories. The resident PR22 job remains STOPPED/HALTED,
> and this source branch does not upgrade, reconfigure or cut over that
> runtime. The next independent step is a generation-fenced, atomically
> admitted resident work queue with tested restart and worker qualification.
> Neither UI labels nor source-only tests prove autonomous background work.

Caveats: user-writable SHA pins and executable paths are not independent trust
roots; authenticated GitHub metadata is a read observation, not merge
authority; model observations are hypotheses requiring human review. Private
repository source is sent only to the user's LOCAL Ollama endpoint and retained
under the current Windows user's local AppData isolated job directory; do not
publish those artifacts. There are no background retries and no paid inference.
