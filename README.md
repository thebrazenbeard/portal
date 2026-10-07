> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# P.O.R.T.A.L.

**Portfolio Orchestration & Repository Tracking Access Layer**

P.O.R.T.A.L. is Vera's **whole-portfolio orchestration and host interface layer**. It coordinates bounded work across repositories, exposes a durable CLI/host bridge, and provides P.O.R.T.A.L. Desktop as a local human interface to the resident Vera runtime.

ChatGPT can be one interaction surface, but it is not a runtime dependency and is no longer the architectural center of P.O.R.T.A.L. Vera acts as the cross-project coordinator; P.O.R.T.A.L. discovers and schedules executable frontiers across active projects; the inherited Project Runner kernel claims, fences, routes, executes, verifies, and reconciles work; independent lanes run in parallel; and every freed lane can be refilled with the next eligible frontier.

The original single-chat design remains a supported interaction pattern and historical architecture input; see [Single-Chat Whole-Portfolio Architecture](docs/architecture/PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md).

## Architecture

- `runner/` is the inherited Project Runner execution engine: exact-subject identity, portfolio cycles, durable queues, budgets, leases, fencing, worker routing, receipts, reconciliation, supervised tasks, and completion verification.
- `portal/` is the P.O.R.T.A.L. portfolio composition layer: whole-portfolio coordination, durable command-session state, live-occupancy-aware node placement, and bounded continuous refill.
- Vera owns cross-project coordination and collision/ownership arbitration.
- Durable state lives outside any one UI or chat context so CLI, Desktop, chat, or another qualified host can reconstruct and resume the portfolio.
- Node placement and schedulability do not create protected-effect authority.

P.O.R.T.A.L. was seeded from Project Runner because the required execution loops largely already exist. The architectural task is to compose them into one whole-portfolio control surface, not to reinvent a second runner.

## Current implementation state

The current `portal/` package now implements the repository-level coordinator core, not just planning. It can discover live portfolio membership, build collision-safe parallel waves, account for already-active subjects and live occupied node slots, place new work only on remaining capacity, acquire exact-head Project Runner claims, persist `run / continue / hold / complete / status / stop` command-session state, independently reconcile owning-substrate evidence before freeing lanes, and refill newly available capacity across successive generations. The host bridge adds expiring route/occupancy/frontier currentness, durable bind-before-dispatch state, atomic attempt-boundary claiming, unresolved-effect recovery, and a reusable host pump for embedding external plugin/workstation drivers.

The durable command session is the preferred operator/control surface for portfolio orchestration. P.O.R.T.A.L. Desktop is the preferred local human interface to the resident Vera runtime. Existing `wave` and `ecosystem` commands remain lower-level execution/proposal surfaces. Source mutation remains separately governed through Project Runner promotion/execution/reconciliation; scheduling does not manufacture effect authority.

This source implementation does not by itself prove that a resident P.O.R.T.A.L. process is installed, selected, or continuously running on any machine. Qualified execution adapters and their concrete host drivers still have to be live, attached, current, target-bound, technically capable and authorized for the exact work they perform. Host observations are active only during their own observation window (`observed_at <= now < expires_at`).


## P.O.R.T.A.L. Desktop and resident Vera runtime

**P.O.R.T.A.L. Desktop** opens on the Portfolio tab, with durable session controls for Run, Continue, Hold selected, Stop, and Refresh. **Run is the normal zero-setup path:** when no profile is configured, Desktop bootstraps a runtime-local live profile from its exact frozen P.O.R.T.A.L. source, refreshes current GitHub owner membership into local runtime artifacts, and creates a conservative local execution-node definition. Repository/work rows show their state, worker route, and verification state. **Advanced profile?** remains available for deliberate custom/debug topology; the [example profile](examples/portal-desktop-profile.example.json) is demonstration data rather than required onboarding.

When local `codex`, `gh`, and `git` executables are all available, Desktop auto-configures the existing `CODEX_GH_PROPOSAL_V1` worker and reports `PROCESS_PROPOSAL / CONFIGURED`. That worker executes bounded proposal generation against the exact admitted head and persists a hash-bound source-tree proposal; it cannot commit, push, merge, deploy, install, change credentials/permissions, or mutate the target ref. Proposal publication remains separately governed through Project Runner review/promotion/authority. If the required worker tools are unavailable, Desktop reports the exact missing capability and remains `ADMISSION_ONLY` rather than inventing a route. See the [desktop runbook](docs/operations/PORTAL_DESKTOP_RUNBOOK_V1.md) and [host bridge runbook](docs/operations/PORTAL_HOST_BRIDGE_RUNBOOK_V1.md) for the execution and external-host boundaries.

Pending exact Project Runner authority requests are surfaced separately in the Runtime pane. An explicit **Approve** action is real authority: the resident host revalidates the durable claim/head/fence and mints the existing HMAC execution grant plus the separate protected-effect grant when required. The click does **not** execute the protected effect; Project Runner still owns promotion/currentness/review/backend verification. **Deny** resolves the request without minting authority. Desktop never auto-provisions authority credentials; optional execution/effect key custody is current-user DPAPI or existing environment configuration, while independent review-key custody remains outside the Desktop surface.

The Conversation tab sends human cognition requests over local IPC to the resident Vera host. Runtime health, component source revisions, selected route, autonomous activity, durable evidence IDs, and pending effects remain visible alongside the portfolio. The resident host owns identity/state composition, routing, and acceptance evidence; closing the desktop window leaves that process running.

The resident-host source composes:

- `vera_core.QualifiedVeraRuntime` as the Vera Mono runtime/state boundary;
- Pre-Active's existing scheduler, observers, daemon/engine, and `autonomous.turn` admission;
- the current Pre-Active `VolitionBridge`, where validated `volition.signal` events can produce endogenous cognition requests without effect authority;
- P.O.R.T.A.L. cognition-route discovery and deterministic route selection;
- durable cognition provenance and Vera host-acceptance receipts;
- human cognition through IPC and internally admitted Pre-Active autonomous cognition through the resident adapter.

Local cognition providers are discovered at runtime rather than hard-coded. Locally installed Ollama models, including a trained Vera model when one is actually present and admissible, can satisfy text cognition without making ChatGPT a runtime dependency. Discovery is not authority: merely finding Codex, a model, or another provider does not authorize paid compute, protected effects, or provider mutation.

The Windows bootstrap binds exact SHAs for all four components and creates an isolated user-local environment. Local no-incremental-paid-compute cognition is denied by default; `-AllowLocalNoPaidCompute` explicitly admits that class and enables full local cognition qualification. The default installation verifies component construction and health without claiming cognition was proved. Logon registration requires the separate `-Activate` operator choice and successful source/health checks. Matching qualified re-runs preserve installed sources and activation; source or policy changes require a separate staged root. The installer does not provision credentials.

Run the desktop shell from an installed/source environment with:

    portal-desktop --runtime-root "PATH_TO_STAGED_RUNTIME"

Or directly:

    python -m portal.desktop_app --runtime-root "PATH_TO_STAGED_RUNTIME"

An installed runtime also provides `PortalDesktop.cmd` and the thin supervisor launcher `StartVeraRuntime.cmd`. Follow [P.O.R.T.A.L. Desktop Runbook V1](docs/operations/PORTAL_DESKTOP_RUNBOOK_V1.md) for exact-revision bootstrap, dry-run, health checks, log paths, and staged upgrades. Source presence still does **not** prove installation, route selection, runtime consumption, or current behavioral qualification.

The desktop/runtime path preserves the same evidence rules as the portfolio coordinator:

`COGNITION != PROTECTED_EFFECT_AUTHORITY`

`SOURCE != INSTALLATION != CURRENT_ROUTE != RUNTIME_CONSUMPTION`

`MODEL_AVAILABLE != MODEL_AUTHORIZED`

## Internal-first donor policy

Before implementing new orchestration machinery, Portal mines Patrick-owned repositories for existing qualified mechanisms. External projects are secondary research inputs.

See [Loop Donor Mining V1](docs/research/PORTAL_LOOP_DONOR_MINING_V1.md).

Primary internal donors currently include Project Runner, Pre-Active, WIP, CCB Base, Intranel, VeraMesh, WorkBridge Commander, WorkBridgeMCP, Vera Mono, Discovery, DriftGuard, Ingest, and Temporal.

## Quick start

    python -m pip install -e '.[dev]'
    project-runner validate
    python -m pytest -q

Create a node manifest:

    schema: PORTAL_EXECUTION_NODES_V1
    nodes:
      - id: lappy
        max_parallel: 2
        allowed_lanes: []
        enabled: true
      - id: worklaptop
        max_parallel: 2
        allowed_lanes: []
        enabled: true

Run a durable portfolio command session (the occupied-node values are a current snapshot, not permanent configuration):

    portal run \
      --session-id portfolio \
      --nodes tests/fixtures/portal-nodes-valid.yaml \
      --max-parallel 8 \
      --occupied-node lappy=54 \
      --occupied-node worklaptop=0

That form performs durable admission/reconciliation/refill but does not invent an execution route. To enable the built-in advisory process-proposal adapter, supply an explicit worker-backend manifest:

    portal run \
      --session-id portfolio \
      --nodes path/to/nodes.yaml \
      --worker-backends path/to/worker-backends.yaml \
      --workspace-root .portal/workers

The process-proposal adapter is capability-routed and exact-target-bound. It can generate and persist advisory source-tree proposals, but it has only `NO_PROTECTED_EFFECT` authority; source publication still requires the separate review/promotion/effect-authority path.

For ChatGPT/plugin/workstation-host execution, use the durable host bridge. The host publishes expiring exact-target route advertisements and fresh node-occupancy snapshots, P.O.R.T.A.L. queues bound dispatch envelopes, and the host crosses the durable attempt boundary before the external effect. The preferred interactive host operation is `portal host take`: it filters to adapter IDs the host can actually drive, revalidates exact target/currentness/capability/authority at attempt time, and atomically marks the selected dispatch attempted before returning it. Owning-substrate evidence then reconciles the result. Attempted unresolved effects remain visible through `portal host unresolved` and are never silently replayed through another route.

Seed one identical host capability across every exact repository target in a project registry without inventing wildcard authority:

    portal host advertise-projects \
      --projects registry/projects.yaml \
      --adapter-id github \
      --route-prefix repo-native \
      --node-id repo-native \
      --capability semantic_work \
      --effect-capability SOURCE_ONLY \
      --authorized-effect NO_PROTECTED_EFFECT \
      --ttl-seconds 300

The batch is atomic and still stores one exact target-bound route per repository. The operator must explicitly provide technical effect capability and authorized effect; bulk advertisement never upgrades authority. Use a stronger `--authorized-effect` only when that exact host/target scope is separately authorized.

Embedded hosts can use `PortalHostPump` instead of shelling through the CLI. The pump consumes only already-bound work, persists the attempt before calling the driver, leaves ambiguous driver failures unresolved, tolerates competing-host attempt races without replay, and propagates process-control exceptions. Source-level integration coverage closes the full loop: admit -> queue -> durable attempt -> owning-driver verification -> free capacity -> refill -> verified idle.

See [Host Bridge Runbook V1](docs/operations/PORTAL_HOST_BRIDGE_RUNBOOK_V1.md) for the full recovery-safe flow, including `--project-runner-tasks`, `--host-node-occupancy`, `host take`, `host pending`, `host unresolved`, reconciliation, and refill.

For a one-read operational diagnosis of a durable session, include host details:

    portal status \
      --state-db .portal/portal.sqlite3 \
      --session-id portfolio \
      --host-details

The host diagnostics are also available through the public `build_host_diagnostics(...)` library API for ChatGPT/web or embedded hosts. The read model separates queued work whose bound route is currently qualified, queued work that needs route refresh, and attempted effects that require reconciliation; it does not attempt, cancel, replay, or free work.

`portal run --session-id ...` performs bounded reconcile/refill generations by default. Add `--once` for one generation. The other top-level control commands are `continue`, `hold`, `complete`, `status`, and `stop`. `portal continue` accepts the same execution-adapter and occupancy-currentness configuration needed by the selected route.

Plan a bounded portfolio wave:

    portal plan \
      --wave portfolio/advancement_wave.public.json \
      --nodes tests/fixtures/portal-nodes-valid.yaml \
      --max-parallel 4 \
      --max-per-lane 2 \
      --max-per-identity 2 \
      --max-per-family 2

The `plan` command remains the read-only planning surface. Command sessions own operator intent/refill state; `wave` and `ecosystem` remain lower-level governed execution/proposal workflows.

## Core invariants

- observation is not authority;
- coordination is not authorization;
- exact evidence outranks convenience pointers;
- one colliding semantic mutation subject has one mutation owner;
- blocked work does not stall unrelated executable work;
- node/tool availability does not manufacture authority;
- stale or ambiguous effects fail closed;
- worker success is not completion until required currentness/effect evidence verifies it;
- fresh-chat recovery comes from durable evidence, not assumed conversational continuity.

## Repository provenance

This repository was bootstrapped from:

- source repository: `thebrazenbeard/project-runner`
- source commit: `04702abbf51aa2920b7d054275619253ea6fa748`
- source tree: `f3f228258bc2de73724c598721bd5aa5beedab95`

The machine-readable binding is in [`.portal-bootstrap.json`](.portal-bootstrap.json).

Project Runner's inherited operator documentation remains in [`PROJECT_RUNNER.md`](PROJECT_RUNNER.md). P.O.R.T.A.L.'s coordination contract is in [`PORTAL.md`](PORTAL.md).
