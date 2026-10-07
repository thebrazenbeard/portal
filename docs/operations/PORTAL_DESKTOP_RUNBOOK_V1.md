# P.O.R.T.A.L. Desktop Runbook V1

P.O.R.T.A.L. Desktop is the local portfolio control surface for a separately resident Vera runtime. The Portfolio tab comes first; Conversation and the runtime panel expose human cognition, selected-route provenance, component versions, autonomous activity, and pending effects. Closing the window leaves the resident host and its Pre-Active/Volition loop running. A ChatGPT session is optional.

This procedure describes the source implementation. Installation, a live process, current admissible routes, and successful cognition are separate claims that require their own evidence. Portfolio admission is also separate from attached worker execution and verified external effects.

## Freeze exact sources before installation

Use Python >=3.12 with Tkinter and Git. Run the bootstrap from an inspected checkout of the intended Portal implementation. Record the exact four revisions rather than letting an installer follow moving branches. These commands read remote heads without changing the checkouts:

```powershell
git ls-remote https://github.com/thebrazenbeard/portal.git refs/heads/main refs/heads/work/portal-coordinator-v1 refs/heads/work/portal-desktop-vera-runtime-v1
git ls-remote https://github.com/thebrazenbeard/vera-mono.git refs/heads/main
git ls-remote https://github.com/thebrazenbeard/pre-active.git refs/heads/main
git ls-remote https://github.com/thebrazenbeard/volition.git refs/heads/main
```

Replace each placeholder below with an inspected 40-character lowercase SHA. The selected Portal SHA must contain this desktop/runtime implementation. Reading a newer upstream head is information for an explicit staged upgrade; it never replaces a qualified runtime automatically.

```powershell
$portalSha = 'EXACT_PORTAL_IMPLEMENTATION_SHA'
$veraMonoSha = 'EXACT_VERA_MONO_SHA'
$preActiveSha = 'EXACT_PRE_ACTIVE_SHA'
$volitionSha = 'EXACT_VOLITION_SHA'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'P.O.R.T.A.L.\runtimes\desktop-v1'
$python = 'PATH_TO_PYTHON_3_12_OR_NEWER_EXE'

.\scripts\Install-PortalVera.ps1 -InstallId desktop-v1 -RuntimeRoot $runtimeRoot -Python $python -PortalSha $portalSha -VeraMonoSha $veraMonoSha -PreActiveSha $preActiveSha -VolitionSha $volitionSha -DryRun
```

Dry-run validates inputs and any existing source/policy binding and prints a JSON plan. It creates no runtime directory, installs no packages, launches no host, and registers no logon task. Keep the reported source map with the intended installation.

## Install and choose cognition policy explicitly

To construct and health-check the resident components with local cognition still denied, run the same command without `-DryRun`. To explicitly admit discovered local no-incremental-paid-compute cognition and require the human/autonomous/Volition local proof, include `-AllowLocalNoPaidCompute` in the dry-run and installation commands:

```powershell
.\scripts\Install-PortalVera.ps1 -InstallId desktop-v1 -RuntimeRoot $runtimeRoot -Python $python -PortalSha $portalSha -VeraMonoSha $veraMonoSha -PreActiveSha $preActiveSha -VolitionSha $volitionSha -AllowLocalNoPaidCompute
```

The installer persists the four exact sources and `cognition_policy.allow_local_no_paid_compute` in `RUNTIME_INSTALL_SPEC.json`, creates `.venv`, verifies import origins and clean source heads, performs component construction, and starts the resident host through the singleton supervisor. The explicit cognition-policy option also runs the local cognition qualification. Finding Codex CLI or an external/API route never admits it automatically. The installer does not install models, change provider credentials, or provision credentials.

The default policy can leave a loaded resident runtime **BLOCKED** with `no_admissible_cognition_route`. That is truthful and preserves cognition requests as unresolved/retryable. It is not a local cognition proof. Even an admitted policy requires an actually available, current, eligible route; a missing provider/model or rejected result cannot be reported as success.

`INSTALLATION_RESULT.json` records installation and activation results. `QUALIFICATION.json`, when full local qualification succeeds, records its runtime/source binding and human, autonomous, and Volition evidence. A matching qualified re-run checks the exact imported-source binding without checking out sources, reinstalling packages, or resetting activation; its retained qualification is labelled as reused rather than newly performed.

## Optional Windows logon activation

Logon activation is a separate explicit operator choice. Only add `-Activate` when the operator intends registration of the named runtime's Windows logon task. Use the same exact SHA and cognition-policy options as the bound root. The installer verifies the runtime before registration, then reads back the scheduled task before reporting activation success. A running root is checked over IPC rather than opening a second set of mutable component stores.

The task runs `StartVeraRuntime.cmd`, which calls the thin supervisor. Windows owns automatic activation; the supervisor verifies process liveness and fresh heartbeat and requests singleton-safe startup when the host is absent. A stale-but-live host is reported degraded and is not automatically duplicated.

Installation without `-Activate` creates no logon task. To start the resident runtime manually:

```powershell
& (Join-Path $runtimeRoot 'StartVeraRuntime.cmd')
```

## Open the desktop and verify health

Use the installed launcher so the UI selects the intended root:

```powershell
& (Join-Path $runtimeRoot 'PortalDesktop.cmd')
```

Or launch that installation's Python explicitly:

```powershell
& (Join-Path $runtimeRoot '.venv\Scripts\python.exe') -m portal.desktop_app --runtime-root $runtimeRoot
```

The runtime panel distinguishes **OFFLINE**, **STARTING**, **ACTIVE**, **DEGRADED**, and **BLOCKED**. Assess the reported reason, heartbeat age, loaded components, exact source revisions, and selected cognition route together. A heartbeat file by itself is not proof of a running host. The desktop requests startup through the supervisor and sends conversation requests to the resident runtime; it does not call a fixed model endpoint.

For a process/heartbeat status snapshot outside the GUI, without requesting startup:

```powershell
& (Join-Path $runtimeRoot '.venv\Scripts\python.exe') -c "import json,sys; from pathlib import Path; from dataclasses import asdict; from portal.desktop_supervisor import RuntimeSupervisor,RuntimeSupervisorConfig; print(json.dumps(asdict(RuntimeSupervisor(RuntimeSupervisorConfig(Path(sys.argv[1]))).status()),sort_keys=True))" $runtimeRoot
```

Use `portal.desktop_host --runtime-root ROOT --check` for actual component construction only when that root is offline. A running resident correctly holds the root singleton; use live status/IPC instead of opening competing stores. Closing and reopening Desktop observes the same durable resident state.

## Connect the portfolio inputs

Copy [the example profile](../../examples/portal-desktop-profile.example.json) into an operator-local directory and edit it. The only supported fields are `wave`, `corpus`, `projects`, `nodes`, `max_parallel`, and `holder`. The first four must point to actual existing files. Relative paths resolve against the profile file's directory. `max_parallel` is an integer from 1 to 64.

The example uses the repository's historical public corpus/wave, project registry, and a test node fixture to show file shapes. Those inputs are not a fresh whole-portfolio census, a discovered workstation inventory, or an authority grant. Replace them with a mutually consistent current wave/corpus, a governed project registry, and the operator's execution-node manifest. Node declarations use `PORTAL_EXECUTION_NODES_V1`; they must reflect real intended capacity and lanes. Do not infer a live worker from a declared node.

In Desktop, select **Choose profile…**, choose the edited profile, and enter the intended session ID. The resident runtime validates and persists the profile as `PORTFOLIO_PROFILE.json`. Use **Run** to admit a session, **Continue** for its next safe generation, **Refresh** to reconstruct status, **Hold selected** to record no-refill intent for that subject, and **Stop** to prevent new admission/refill. Hold and Stop preserve active or unresolved effects; they do not erase or automatically cancel dispatched work.

The current Desktop portfolio path reports `dispatch_mode: ADMISSION_ONLY`. **Queued work awaits an attached worker.** It does not provision a worker backend or credentials. Attach live drivers and fresh exact-target routes through the existing [Host Bridge Runbook V1](PORTAL_HOST_BRIDGE_RUNBOOK_V1.md); carry the exact session/state database through that process. The desktop resident's Portal database is `state/portal/session.sqlite3` under the selected runtime root. Worker output counts as completed repository work only after owning-substrate verification and reconciliation.

## Durable evidence, recovery, and upgrades

The runtime stores Vera state, Pre-Active/Volition state, Portal sessions, and cognition evidence under `state/`. The bridge retains submission identity, claimed requests, cancelled requests, and responses. A timeout before successful host claim is cancelled atomically; a claimed request with no response has an unknown outcome. Reusing the same ID and same request observes retained evidence or waits for the claim; changing its payload is rejected. Never blindly replay an unknown attempted effect.

Inspect `logs/install.log`, `logs/runtime.stdout.log`, and `logs/runtime.stderr.log` when installation/startup fails. Installation errors do not establish activation success. A task-readback failure requires inspection of the named Windows task before retrying; do not assume task creation had no effect. Component revisions and imported package locations must agree with the manifest. Source drift fails closed.

For upgrades, select a new installation ID and a new user-local runtime root, freeze the new four revisions, and dry-run first. A different source spec or cognition policy is rejected in an existing bound root. Stage and qualify the new runtime before making an explicit activation choice. Do not replace executing editable sources or silently copy/migrate existing Vera state into another qualified runtime.

Cognition and portfolio admission carry no protected-effect authority. The pending-effects display is observable state, not an approval grant or automatic executor. Exact effect, target, and scope still require Patrick's explicit authority through the governed effect path. No merge, public deployment, credential/permission mutation, paid compute, destructive state change, or private publication is authorized by installing or operating this shell.
