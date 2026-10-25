> **License:** Source-visible, not open source. Original material is proprietary. Commercial use, redistribution, hosted-service use, and commercial derivative products require written permission. See [LICENSE](LICENSE) and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md). Separately identified third-party components retain their own licenses.

# PORTAL

**Portfolio Orchestration & Repository Tracking Access Layer**

PORTAL is Vera's **whole-portfolio orchestration and host interface layer**. It coordinates bounded work across repositories, exposes a durable CLI/host bridge, and provides PORTAL Desktop as a local human interface to the resident Vera runtime.

ChatGPT can be one interaction surface, but it is not a runtime dependency and is no longer the architectural center of PORTAL Vera acts as the cross-project coordinator; PORTAL discovers and schedules executable frontiers across active projects; the inherited Project Runner kernel claims, fences, routes, executes, verifies, and reconciles work; independent lanes run in parallel; and every freed lane can be refilled with the next eligible frontier.

The original single-chat design remains a supported interaction pattern and historical architecture input; see [Single-Chat Whole-Portfolio Architecture](docs/architecture/PORTAL_SINGLE_CHAT_WHOLE_PORTFOLIO_V1.md).

## Architecture

- `runner/` is the inherited Project Runner execution engine: exact-subject identity, portfolio cycles, durable queues, budgets, leases, fencing, worker routing, receipts, reconciliation, supervised tasks, and completion verification.
- `portal/` is the PORTAL portfolio composition layer: whole-portfolio coordination, durable command-session state, live-occupancy-aware node placement, and bounded continuous refill.
- Vera owns cross-project coordination and collision/ownership arbitration.
- Durable state lives outside any one UI or chat context so CLI, Desktop, chat, or another qualified host can reconstruct and resume the portfolio.
- Node placement and schedulability do not create protected-effect authority.

PORTAL was seeded from Project Runner because the required execution loops largely already exist. The architectural task is to compose them into one whole-portfolio control surface, not to reinvent a second runner.

## Current implementation state

The current `portal/` package now implements the repository-level coordinator core, not just planning. It can discover live portfolio membership, build collision-safe parallel waves, account for already-active subjects and live occupied node slots, place new work only on remaining capacity, acquire exact-head Project Runner claims, persist `run / continue / hold / complete / status / stop` command-session state, independently reconcile owning-substrate evidence before freeing lanes, and refill newly available capacity across successive generations. The host bridge adds expiring route/occupancy/frontier currentness, durable bind-before-dispatch state, atomic attempt-boundary claiming, unresolved-effect recovery, and a reusable host pump for embedding external plugin/workstation drivers.

The durable command session is the preferred operator/control surface for portfolio orchestration. PORTAL Desktop is the preferred local human interface to the resident Vera runtime. Existing `wave` and `ecosystem` commands remain lower-level execution/proposal surfaces. Source mutation remains separately governed through Project Runner promotion/execution/reconciliation; scheduling does not manufacture effect authority.

This source implementation does not by itself prove that a resident PORTAL process is installed, selected, or continuously running on any machine. Qualified execution adapters and their concrete host drivers still have to be live, attached, current, target-bound, technically capable and authorized for the exact work they perform. Host observations are active only during their own observation window (`observed_at <= now < expires_at`).


## PORTAL Desktop and resident Vera runtime

The repository also contains the source for **PORTAL Desktop**, a thin human-facing shell for a host-composed Vera runtime. The desktop window is deliberately not the model router or identity root: it sends requests over the existing local IPC bridge to the resident host, which owns cognition routing, provenance, runtime state, and acceptance evidence.

The resident-host source composes:

- `vera_core.QualifiedVeraRuntime` as the Vera Mono runtime/state boundary;
- Pre-Active scheduler/observer state and `autonomous.turn` admission;
- the current Pre-Active `VolitionBridge`, where validated `volition.signal` events can produce endogenous cognition requests without effect authority;
- PORTAL cognition-route discovery and deterministic route selection;
- durable cognition provenance and Vera host-acceptance receipts;
- the same IPC path for human and autonomous cognition.

Local cognition providers are discovered at runtime rather than hard-coded. Locally installed Ollama models, including a trained Vera model when one is actually present and admissible, can satisfy text cognition without making ChatGPT a runtime dependency. Discovery is not authority: merely finding Codex, a model, or another provider does not authorize paid compute, protected effects, or provider mutation.

The Windows staged installer binds exact source SHAs, creates an isolated environment, launches a staged resident host, verifies component loading and cognition, and only registers logon activation after qualification succeeds. Source presence still does **not** prove installation, route selection, runtime consumption, or current behavioral qualification.

Run the desktop shell from an installed/source environment with:

    portal-desktop

Or directly:

    python -m portal.desktop_app

### Windows operator-owned staged launch

`portal-desktop` alone does **not** install or start a resident Vera host. The staged
installer requires Windows, Python 3.12+ with Tkinter, Git, network access to
the four source repositories, and an **already running local text model**
(normally Ollama on `127.0.0.1:11434`, or a compatible qualified local provider
on `127.0.0.1:18081`). Model availability and response compatibility must be
checked on the actual machine; no model download or service installation is
performed by this procedure.

From a freshly inspected checkout of `thebrazenbeard/portal@main`, use
PowerShell to resolve and inspect the exact four source revisions before
allowing the installer to clone, install, or start anything:

```powershell
$sourceHeads = @{}
foreach ($repo in @('vera-mono', 'portal', 'pre-active', 'volition')) {
    $line = git ls-remote "https://github.com/thebrazenbeard/$repo.git" 'refs/heads/main'
    if ($LASTEXITCODE -ne 0 -or -not ($line -match '^([0-9a-f]{40})\s+refs/heads/main$')) {
        throw "Cannot resolve exact main revision for $repo"
    }
    $sourceHeads[$repo] = $Matches[1]
    Write-Host "$repo $($sourceHeads[$repo])"
}
if ((git rev-parse HEAD).Trim() -ne $sourceHeads['portal']) {
    throw 'Local PORTAL checkout is not the resolved current main; inspect/update first'
}
```

After reviewing those hashes and the source, select a **dedicated leaf**, for
example `D:\VERA\PortalDesktop\desktop-1` (never `D:\VERA` itself). Create
an **empty, real (not symlinked/junction) leaf** and inspect its ACL **before**
installing; verify the intended account controls it and untrusted users cannot
read or write it. Check it again after
staging. Do not proceed on a shared or permissive folder; these commands do not
change Windows ACLs for you. Use a simple ASCII path: the launcher rejects
percent signs, quotes, delayed-expansion marks, and CMD metacharacters rather
than creating a command script that could reinterpret them. Installation
accepts only the four canonical `thebrazenbeard` component repositories;
exact hashes still need independent inspection because pinning alone does not
make upstream code trustworthy.

```powershell
$runtimeRoot = 'D:\VERA\PortalDesktop\desktop-1'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
icacls $runtimeRoot
# Review the ACL output and stop if other users have access.
py -3.12 -m portal.desktop_installer `
    --runtime-root $runtimeRoot `
    --install-id desktop-1 `
    --portal-ref main `
    --portal-sha $sourceHeads['portal'] `
    --vera-mono-sha $sourceHeads['vera-mono'] `
    --pre-active-sha $sourceHeads['pre-active'] `
    --volition-sha $sourceHeads['volition']

# Only after installer qualification succeeds:
icacls $runtimeRoot
& "$runtimeRoot\PortalDesktop.cmd"
```

This command deliberately omits `--activate`: it starts a staged host for
qualification but does **not** register a logon task. `--activate` separately
requests persistent logon activation and should be used only after inspecting
the qualification and installation receipts. A new exact source set requires
a new install leaf; a nonempty leaf is rejected even when the SHAs match. An
interrupted install that has cloned a component is **not resumable** by this
installer: inspect its evidence and choose a new leaf. It never resets, cleans,
or overwrites an existing source checkout. Every fresh checkout must be clean
(including untracked and ignored files) before editable installation.

**Security boundary:** the file IPC bridge currently depends on filesystem
permissions, not authenticated requests; someone able to write its request
directory can submit cognition requests and a `shutdown` command. The
`state/cognition/desktop-cognition.sqlite3` ledger retains plaintext prompts
and responses (including SQLite sidecar files while active). Never place
credentials in conversation prompts, make the runtime leaf multi-user writable,
or infer privacy simply because inference uses a loopback address. Local
model discovery is not proof that a provider performs every operation offline.

The generated `PortalDesktop.cmd` binds the installed runtime root explicitly.
The bare `portal-desktop` command uses an environment/default root and may not
point at this staged installation. A qualified receipt proves only that its
bounded checks passed on that exact installed subject; it does not establish
continuing operation, AI advancement, protected-effect authority, or a direct
connection to this ChatGPT session.

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

For ChatGPT/plugin/workstation-host execution, use the durable host bridge. The host publishes expiring exact-target route advertisements and fresh node-occupancy snapshots, PORTAL queues bound dispatch envelopes, and the host crosses the durable attempt boundary before the external effect. The preferred interactive host operation is `portal host take`: it filters to adapter IDs the host can actually drive, revalidates exact target/currentness/capability/authority at attempt time, and atomically marks the selected dispatch attempted before returning it. Owning-substrate evidence then reconciles the result. Attempted unresolved effects remain visible through `portal host unresolved` and are never silently replayed through another route.

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

Project Runner's inherited operator documentation remains in [`PROJECT_RUNNER.md`](PROJECT_RUNNER.md). PORTAL's coordination contract is in [`PORTAL.md`](PORTAL.md).

### Vera-branded desktop appearance

The PORTAL Desktop frontend preserves its local cognition and governance
boundaries while using a styled dark surface rather than default Tk widgets.
The compact logo asset at `portal/assets/vera_icon.png` is a downsized
copy of the user-provided Vera emblem; the burgundy-and-rose palette
references the companion Vera PC wallpaper. The wallpaper is design
reference material, not a runtime dependency or a claim about model identity.

The UI attributes model answers to their actual cognition route; it does
not claim a different local model is the current ChatGPT session. Desktop
status polling uses a separate short-timeout client, and message requests
retain their longer timeout. Neither appearance nor branding grants
protected-effect authority.
