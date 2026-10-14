# PORTAL Windows — Exact-source local project run V1

## Scope

The Windows Portfolio view lists authenticated live GitHub repositories, but discovery is not execution authorization.
Only a separately reviewed, exact-SHA-bound project profile can enable Run selected.
The only initial profile is the public thebrazenbeard/physica repository at
a33e3a8c94787cc884dd7c668b709ff1ec945551, using the 10-step numerical
oscillator simulation (no training or hardware).

The profile is in registry/desktop_run_profiles_v1.json. The approved module
and argument vector are hard-coded and independently validated in
portal/desktop_project_runs.py. The manifest is not an arbitrary command language.

At operator click, the launcher checks the live GitHub main SHA, checks out
the exact detached SHA into an isolated user-local cache, rejects dirty source,
and runs a 25-second no-shell process with minimal inherited environment.
The program output must match the reviewed Physica simulation schema, model
and 64-hex digest. Attempts receive an ATTEMPTED receipt before process launch;
final receipt states are VERIFIED_COMPLETE, VERIFIED_HELD, or OUTCOME_UNKNOWN.
Recent runs is a read-only display. Unfinished attempts are never replayed.

## Hostile review — accepted

Pinning a commit and stripping inherited credentials DOES NOT sandbox Python.
The reviewed program retains whatever filesystem/network OS authority its
Windows user has. LOCAL_SIMULATION_ONLY describes intent, not confinement;
the receipt declares os_sandboxed false. Do not expand this interface to
arbitrary repositories, paid compute, destructive actions, or unreviewed code.

This pilot is not an alternative to PORTAL Project Runner's governed execution
and qualified host-adapter boundary. Run curated cycle uses the actual PORTAL
command-session mechanism with NO_EFFECT ceiling and must fail closed when
no qualified execution route exists. The two operations are intentionally distinct.

## Evidence and limits

A bounded Lappy live Physica run produced a durable VERIFIED_COMPLETE receipt
for ten oscillator steps at the pinned exact SHA; this is software execution,
not physical validation. Unit tests cover wrong heads, manifest injection,
unsupported project launches, timeout, failed subprocess, receipt integrity,
no auto-retry, and the GUI's per-project gating.

General portfolio execution still needs typed project-specific worker routes,
authorization, custody, reconciliation, logs, cancellation and OS isolation.
No installation, logon task mutation, provider change, or merge is implied by
this source-only work.
