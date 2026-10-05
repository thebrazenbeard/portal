> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# Project Runner

Public-safe orchestration kernel for Patrick's multi-repository project ecosystem.

> Redundant? No. It's just an extra redundancy.

Project Runner answers: what exists, what changed, what depends on the change, what work is justified and authorized, what can execute now, and what evidence demonstrates completion.

## Current state: M6 bounded execution fabric

M1 established typed project/worker registries. M2 added exact observations, dependency/currentness logic, and invalidation propagation. M3 added frontier generation, semantic deduplication, collision grouping, and deterministic priority. M4 added bounded recursive work units, budgets, fenced leases, mock dispatch, and exact-subject completion checks.

M5 adds the first real GitHub execution route:

- JSON-bound operation payloads are part of semantic work identity;
- GitHub REST transport supports exact ref reads, branch creation, and UTF-8 file writes;
- technical backend capability and target repository authority are independent gates;
- target grants constrain repository, operation, ref, and path scope;
- mutations require exact expected-head/blob preconditions;
- successful writes require post-write ref/file readback;
- SQLite-backed lineage budgets persist across process/workflow boundaries with generation CAS;
- SQLite-backed leases persist claim/reclaim/completion state and monotonic fencing tokens;
- recursive work identity, parent linkage, exact ancestry, budget scope, lifecycle status, and generation survive restart in a digest-verified SQLite record;
- recursive child admission is one SQLite transaction: parent-budget CAS, child-budget creation, and child-work/ancestry persistence either all commit or all roll back;
- ordinary child budget/work initialization is rejected so callers cannot bypass the atomic admission path;
- each durable work record integrity-binds its effective capability ceiling; child admission derives the parent ceiling from durable state, so a restarted caller cannot widen inherited capabilities;
- pre-ceiling legacy recursive records are accepted only after their legacy integrity digest verifies, then migrate conservatively to their recorded required-capability set;
- durable dispatch admission commits budget reservation, lease claim/fencing token, and WorkUnit `CLAIMED` state in one SQLite transaction **before** backend execution;
- a crash after durable dispatch admission cannot restore the reserved active/backend-job quota or forget the owning lease/work state;
- retry/unknown redispatch consumes retry quota while reclaiming with a higher fence;
- the atomic boundary reconstructs the durable parent and re-runs decomposition/capability narrowing rather than trusting a caller-supplied `ChildAdmission`;
- lease-bound nonterminal work states (`CLAIMED`, `RUNNING`, `VERIFYING`, `FAILED_RETRYABLE`, `OUTCOME_UNKNOWN`) require the exact current holder/fencing token and an unexpired lease at transition time;
- retryable/unknown outcomes therefore cannot be asserted by a non-owner; after expiry, a reclaimed higher fence may resume them through the allowed lifecycle;
- durable lifecycle follows an explicit forward state machine; ownership never authorizes forward skips or backward active transitions;
- terminal finalization is atomic: verification proposes the terminal outcome without mutating the lease, then one SQLite transaction finalizes the exact lease fence and WorkUnit terminal status together;
- direct terminal status CAS is rejected, eliminating the crash window that could strand `VERIFYING` beside a separately completed lease;
- durable work status cannot roll terminal states back or reset active work to `PENDING`;
- stale generations/fences fail closed across both the lease table and the durable WorkUnit lifecycle;
- CI exercises the actual GitHub backend against the workflow repository in read-only mode;
- M4 post-work currentness and independent completion-evidence rules remain in force.

The live CI route still has only `contents: read`; it proves exact-head GitHub connectivity and the read-only HC Brain → Transcendence M6 path without manufacturing downstream mutation authority.

## Quick start

    python -m pip install -e '.[dev]'
    project-runner validate
    project-runner inventory
    project-runner dispatch-report --before tests/fixtures/observations-before.yaml --after tests/fixtures/observations-after.yaml --dependencies tests/fixtures/m3-dependencies.yaml
    project-runner github-read-smoke --repository thebrazenbeard/project-runner --ref main
    python -m pytest -q

## Governed read/write execution

Project Runner has both read-only inspection routes and a narrowly scoped GitHub
source-write backend. After a portfolio claim is durably promoted to `RUNNING`
with an exact signed execution request, separate review, protected-effect
authority, and a current fencing token, the operator can invoke:

    project-runner github-source-write-execute \
      --state-db .project-runner/project-runner.sqlite3 \
      --lineage-id <lineage-id> \
      --work-fingerprint <fingerprint> \
      --fencing-token <token>

This command requires `PROJECT_RUNNER_GITHUB_TOKEN` in the environment. It
recovers the exact existing promotion from durable storage: callers cannot
supply fresh repository, ref, path, or content arguments. Runtime execution
rechecks lease, review and authority expiry, live source head, expected blob,
non-force ref CAS, and post-publication readback. An uncertain outcome is
recorded as `OUTCOME_UNKNOWN` and must be reconciled, never blindly replayed.
The current adapter supports only `PUT_FILE` on an exact authorized branch;
it does not merge PRs, deploy, or grant itself new permissions.

## Real operator path

The ordinary CLI now has one deliberately narrow real execution route: a durable, read-only M6 GitHub inspection. It derives one READY INSPECT frontier, persists budget/work/lease/journal state before and during execution, performs exact-ref reads under explicit grants, independently rechecks currentness, and atomically finalizes terminal evidence.

    export PROJECT_RUNNER_GITHUB_TOKEN=<token-with-required-read-access>
    project-runner run-inspection \
      --before <previous-observations.yaml> \
      --after <current-observations.yaml> \
      --dependencies <dependencies.yaml> \
      --project <consumer-project-id> \
      --target-repository <owner/repository> \
      --target-ref <branch> \
      --target-head <exact-40-hex-head> \
      --state-db .project-runner/project-runner.sqlite3

Interrupted or unresolved durable attempts are visible without backend re-execution:

    project-runner operator-status --state-db .project-runner/project-runner.sqlite3

This first operator route is intentionally read-only. A GitHub token's technical permissions do not manufacture write, merge, or deploy authority. The SQLite file is durable only on the filesystem that retains it; an ephemeral CI runner does not become a persistent orchestrator merely because SQLite was involved.

See docs/OPERATOR_EXECUTION_V1.md.

## Local background-task monitor (Windows / Lappy)

Project Runner keeps an explicit local registry for background processes it owns and
also discovers several live ChatGPT execution trees (Executor, Codex bridge workers,
and named VERA workers) without pretending that every generic Python/PowerShell
process belongs to ChatGPT.

Launch a tracked background task from **any working directory** with:

    project-runner task-start `
      --name "model-training-lane-b" `
      --repository "thebrazenbeard/vera_model_training" `
      --lane "lane-b" `
      --working-directory "C:\path\to\vera_model_training" `
      --command "python train.py"

The CLI and all task-status/history/reconcile commands share the same default task
root: `PROJECT_RUNNER_TASKS_ROOT` when set, otherwise
`%LOCALAPPDATA%\ProjectRunner\tasks` on Windows. They therefore do not depend on
the shell's current directory.

`task-start` uses `cmd.exe` by default for bounded noninteractive execution.
Use `--shell powershell` only when the task command requires PowerShell syntax.
The checkout-local `scripts\Start-ProjectRunnerTask.ps1` compatibility launcher
retains its historical PowerShell command semantics.

Tracked launches run through the Python `runner.task_supervisor` module. The
supervisor registers itself as the live task root, launches the requested command,
waits for it to terminate, captures the real process return code, and writes a terminal receipt.
Exit code `0` becomes `COMPLETED`; a nonzero exit code becomes `FAILED`.
Terminal tasks are atomically moved from `active\` to `history\` instead of
remaining in the live monitor as dead PIDs.

By default Windows state is stored under
`%LOCALAPPDATA%\ProjectRunner\tasks`; set `PROJECT_RUNNER_TASKS_ROOT` or pass
`--tasks-root` to a Project Runner task command to select another local state
directory.

Open the live dashboard with:

    powershell -NoProfile -File .\scripts\Watch-ProjectRunnerTasks.ps1

Use `-Once` for one snapshot, `-IncludeCommand` to display command metadata,
and `-History` to display terminal task history below the live set. The live
dashboard reports registered Project Runner tasks plus discovered ChatGPT process
trees and labels their attribution strength (`EXACT`, `STRONG`, or
`HEURISTIC`).

An `ORPHANED` record is now exceptional: the registered supervisor disappeared
before an authoritative terminal receipt was written. Reconcile such stale records
without inventing an exit code with:

    project-runner task-reconcile --tasks-root "$env:LOCALAPPDATA\ProjectRunner\tasks"

Reconciliation moves dead unresolved records to history as `UNKNOWN_EXIT` with
`PROCESS_GONE_WITHOUT_FINAL_RECEIPT`. Historical state is available as JSON with:

    project-runner task-history --tasks-root "$env:LOCALAPPDATA\ProjectRunner\tasks"

The monitor remains observability-only: it has no stop/kill action.

## Multi-lane scheduling

Project Runner can bound independent progress streams without creating separate authority domains. Advancement items may declare an optional `lane`; otherwise their lead identity is the effective lane. The wave planner keeps collision keys global and can enforce global, per-lane, per-identity, and per-family capacity at the same time.

```bash
project-runner portfolio-wave-plan \
  --max-parallel 6 \
  --max-per-lane 2 \
  --max-per-identity 2 \
  --max-per-family 2
```

Lane placement is scheduling metadata, not durable work identity or permission. The plan digest binds the lane assignment, while the operator bridge continues to bind the underlying work subject independently.

Local supervised tasks can also be inspected per lane:

```powershell
project-runner task-status --lane lane-b
project-runner task-history --lane lane-b
```

The task monitor binds a recorded Windows supervisor PID to its process creation time when available, preventing PID reuse from masquerading as the original task. See `docs/MULTI_LANE_TASK_HANDLING_V1.md`.

## Durable portfolio currentness

Project Runner can now collect registered dependency refs itself and persist the resulting scheduler state:

    export PROJECT_RUNNER_GITHUB_TOKEN=<token-with-required-read-access>
    project-runner portfolio-cycle \
      --dependencies topology/dependencies.yaml \
      --state-db .project-runner/project-runner.sqlite3

The first cycle for a dependency-topology digest establishes an observation baseline and schedules nothing. Project- or worker-registry changes on the same topology preserve observation continuity: unresolved exact-subject work is recovered from durable history, re-evaluated under current capabilities/scheduling/target/worker-route authority, and carried forward when still relevant. Later cycles atomically persist the new snapshot plus READY/BLOCKED scheduler decisions.

    project-runner portfolio-status \
      --state-db .project-runner/project-runner.sqlite3

Collection is read-only. Dependency selectors manufacture neither provider scope nor write authority: provider/consumer IDs must exist in the current project registry, selector repositories must belong to the declared provider, refs must be exact, and READ_REF grants are derived only after those checks. If any required read fails, the durable snapshot does not advance.

The durable queue now has a bounded read-only consumer. Projects declare exact `execution_targets` by work type; without one, otherwise-runnable work becomes `WAITING_AUTHORITY`.

    project-runner consume-queue \
      --dependencies topology/dependencies.yaml \
      --state-db .project-runner/project-runner.sqlite3

Queue claims use monotonic fencing and collision-domain exclusion. Unchanged observation cycles do not erase pending queue work; compatible historical rows remain claimable only while their full exact provider subject is still current. The consumer binds the declared target ref to an exact current commit and persists that binding. INSPECT may use the built-in durable GitHub read operator. Other supported read-only work types require an explicitly bound worker whose exact route is VERIFIED and whose route contract is READ_ONLY. Reclaimed INSPECT claims reconcile existing terminal operator state without re-execution; nonterminal durable operator state becomes `OUTCOME_UNKNOWN` rather than being blindly retried.

    project-runner queue-status \
      --state-db .project-runner/project-runner.sqlite3

Read-only worker handoffs are persisted in a digest-bound outbox and can be summarized without invoking a worker:

    project-runner worker-route-status \
      --state-db .project-runner/project-runner.sqlite3

A qualified worker route uses a fenced pull/receipt protocol. Delivery rechecks the current worker/route qualification and the provider exact subject; superseded provider work is retired before a worker can pull it. The packet is written to a `0600` owner-only file on POSIX, or to a file with explicit owner-only NTFS permissions on Windows, rather than echoed to ordinary logs. On Windows, inherited access is removed from a fresh temporary file before its private contents are written. With an external/private project registry, the packet must be written outside the public checkout:

    project-runner claim-worker-route \
      --worker-id <worker-id> \
      --route <verified-route> \
      --holder <claimant-id> \
      --payload-out /secure/path/packet.json \
      --state-db .project-runner/project-runner.sqlite3

A built-in reference endpoint exercises the same delivery protocol with real GitHub READ_REF currentness checks:

    PROJECT_RUNNER_GITHUB_TOKEN=<read-token> \
    project-runner run-reference-worker \
      --holder project-runner-reference-read-worker \
      --state-db .project-runner/project-runner.sqlite3

The reference worker is read-only and emits only route/receipt summary metadata.

    project-runner record-worker-receipt \
      --route-id <route-id> \
      --worker-id <worker-id> \
      --route <verified-route> \
      --holder <same-claimant-id> \
      --expected-fencing-token <delivery-fence> \
      --receipt-class SUCCEEDED \
      --receipt-sha256 <sha256> \
      --state-db .project-runner/project-runner.sqlite3

SAFE routes may reclaim expired delivery claims. RECONCILE_REQUIRED routes freeze expired claims into ambiguity until explicit reconciliation; NEVER routes are not replay-releasable.

Ambiguous or routed queue items retain their collision reservation until explicit evidence-bound reconciliation:

    project-runner reconcile-queue \
      --snapshot-id <id> \
      --frontier-fingerprint <sha256> \
      --expected-fencing-token <token> \
      --resolution CONFIRM_COMPLETE \
      --evidence-sha256 <sha256> \
      --reconciler <identity> \
      --state-db .project-runner/project-runner.sqlite3

A read-only retry release advances the durable attempt generation and uses a new deterministic lineage. SAFE routes permit automatic expired-claim replay and explicit retry release; RECONCILE_REQUIRED permits retry only through explicit reconciliation; NEVER does not permit retry release. Routed `CONFIRM_COMPLETE` also re-reads both the provider ref and bound consumer target ref live—worker success alone is not completion.

The committed public registry binds Project Runner's built-in `INSPECT` target and a `REREVIEW` target to the Project Runner reference read worker. The 12 Custom GPT records remain REGISTERED with UNVERIFIED routes and are not silently activated. The reference worker is separate: a GITHUB_ACTION worker with a VERIFIED `RUNNER_ACTION_PULL` READ_ONLY/SAFE route that is live-proven in CI. Other work remains blocked until its target and qualified route are explicitly declared; Project Runner does not infer `main`, choose the first repository, or manufacture routing authority.

## Private portfolio registry

The committed `registry/projects.yaml` is a public-safe seed. It may name repositories that were already part of Project Runner's historical public baseline, but it must not expand the public repository with additional private project identifiers merely because those projects are relevant to orchestration.

For a complete private portfolio, supply a **complete replacement registry** from outside this checkout and pin the exact expected bytes:

    PROJECT_RUNNER_PROJECT_REGISTRY=/absolute/private/path/projects.yaml \
    PROJECT_RUNNER_PROJECT_REGISTRY_SHA256=<lowercase-sha256> \
    PROJECT_RUNNER_PRIVATE_COLLISION_KEY=<private-64-hex-key> \
    project-runner inventory

The override must be an absolute path and its exact SHA-256 must match before the registry is accepted. Project Runner does not merge a hidden/private layer into the committed seed.

Private frontier derivation requires `PROJECT_RUNNER_PRIVATE_COLLISION_KEY`, a separate stable 256-bit runtime key used only to derive opaque collision domains. The registry digest is a currentness binding, not secret key material. Keep the collision key outside public Git, logs, receipts, and workflow artifacts. Reuse the same key across restarts that must preserve private semantic/collision identity; deliberate key rotation creates a new private collision namespace.

Every external project record must explicitly declare assignment scope, review scope, family, and `scheduling_state`. `HELD` projects contribute no runnable capabilities even if capabilities are listed in the record; only `SCHEDULABLE` records may contribute to ready frontier derivation.

Detailed `evaluate-change`, `frontier-report`, and `dispatch-report` CLI output is disabled while an external registry is selected, preventing private project IDs, repository subjects, dependency IDs, or collision keys from being emitted into ordinary logs. Inventory/validation remain count-only.

`frontier-summary` is the private-safe visibility surface: it emits only total, READY, non-READY, and per-status counts. It never emits project IDs, subjects, dependency IDs, collision keys, reasons, or input paths, and external-input failures collapse to a generic structural-error message.

## Authority model

Backend capability answers "can this route technically perform an operation?"

Target authority answers "is this operation authorized for this exact repository/ref/path?"

Both must pass. Connector/token permissions do not manufacture governance authority.

Core invariants:

- observation is not authority;
- coordination is not authorization;
- exact evidence outranks convenience pointers;
- blocked work remains visible without stalling unrelated work;
- semantic work identity ignores incidental IDs/order but includes material payload;
- child capability and budget only narrow;
- persistent budgets cannot silently reset between workflows;
- semantic claims use atomic leases and monotonic fences;
- stale workers cannot complete reclaimed work;
- mutations use expected-state checks and exact readback;
- backend success is not completion until currentness and completion evidence are independently verified;
- public repository state remains public-safe.

See `docs/superpowers/plans/2026-09-17-m5-github-backend.md`.

## Worker recovery

Project Runner and its dispatched workers do not require permanent ChatGPT conversations. Fresh runtimes reconstruct from durable repository/Bus/currentness/authority evidence; chat URLs and Custom GPT share links are locators or provenance only. See `docs/PROJECT_RUNNER_WORKER_RECONSTRUCTION_V1.md`.
