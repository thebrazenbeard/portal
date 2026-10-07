# P.O.R.T.A.L. Desktop + Vera Runtime continuation

Branch: `work/portal-desktop-vera-runtime-v1`
Base coordinator at start: `a4cf02d34c2f8c4ac2b1e60161cc53e9845616f0`
Canonical main remains untouched.

## Product boundary

P.O.R.T.A.L. Desktop is the human interface and lifecycle shell for a resident Vera runtime. It is not the identity root, model, canonical-memory authority, or protected-effect authority.

```
Patrick <-> P.O.R.T.A.L. Desktop <-> local IPC <-> Vera resident runtime
                                            |- vera_core / QualifiedVeraRuntime
                                            |- Pre-Active
                                            |- Volition
                                            |- P.O.R.T.A.L.
                                            |- cognition adapters
```

ChatGPT is optional. Closing the desktop window does not stop the resident runtime.

## Implemented

- Runtime supervisor with OFFLINE / STARTING / ACTIVE / DEGRADED / BLOCKED states.
- ACTIVE requires a live PID, fresh heartbeat, and all four components loaded.
- Stale-but-live runtime is DEGRADED and is not duplicated.
- Cognition route discovery for local Ollama and Codex CLI.
- Local/no-incremental-paid-compute Ollama routes may be auto-admissible for text cognition.
- Codex CLI is discoverable but not auto-admissible merely because the executable exists.
- Durable SQLite cognition ledger with route/provider/model provenance.
- Human and Pre-Active cognition share the same runtime route-selection path.
- Reused completed request IDs are idempotent; mismatched reuse is rejected.
- Missing route / adapter failures remain retryable instead of being falsely acknowledged.
- Cognition records hard-code protected-effect authority false.
- `QualifiedVeraRuntime` host acceptance callback persists a digest-bound `ACCEPTED_HOST_OBSERVATION` receipt.
- Vera host acceptance explicitly does not claim canonical-memory promotion.
- Existing filesystem request/response bridge is reused for desktop IPC.
- IPC distinguishes timeout-before-claim from claimed-but-response-missing ambiguity.
- Thin Tkinter shell with conversation, health, route, autonomous activity, activity log, and pending-effect area.
- GUI sends cognition through resident IPC and does not invoke a model directly.
- Exact four-source staged install contract rejects source drift.
- Resident host composes Vera Mono, Portal, Pre-Active scheduler/observers, VolitionBridge, cognition ledger, local route discovery, and desktop IPC.
- Autonomous host claims only `autonomous.turn`; unrelated Pre-Active events cannot be stolen.
- Only COMPLETED cognition is ACKed; failed/unresolved cognition uses Pre-Active retry/dead-letter semantics.
- `volition.signal` uses current upstream `VolitionBridge`; endogenous turns preserve `effect_authority=false`.
- Windows installer stages exact SHAs, creates isolated venv, verifies imports, launches resident host, qualifies it, and only then registers logon activation.
- Windows launchers created for resident host and desktop app.

## Test evidence

Latest source-unit verification before staging:
- desktop supervisor/cognition/runtime/IPC/app/install/pre-active/volition/acceptance surfaces: 44 passed at installer checkpoint.
- state-layout regression: 10 focused tests passed after first staged failure.
- installer metadata reconciliation tests: 5 focused tests passed.
- no paid CI was intentionally initiated; checkpoint commits use `[skip ci]`.

## Staged-install history

### Failed stage retained as evidence

Runtime root:
`C:\Users\patri\AppData\Local\VeraDesktopRuntime\20261007-aa3d904`

Portal source:
`aa3d904aae669ecee0c9fdc52ff1a24b88ee3059`

Failure:
- resident host crashed before heartbeat because component SQLite parent directories were absent.
- qualification did not pass.
- no logon activation task was registered.
- root was not repurposed after failure.

Regression fix:
`7b4b5b4cb0c1aff9e41d8ba657104f8b369402af`

### Qualified active runtime

Runtime root:
`C:\Users\patri\AppData\Local\VeraDesktopRuntime\20261007-7b4b5b4`

Runtime ID:
`c75d0921114d46b248e159948e0b39215d69d74205812d25881073e49e19d74b`

Frozen source binding:
- Vera Mono: `e5af8cb740267bb5674864571e915842bf5e6677`
- P.O.R.T.A.L.: `7b4b5b4cb0c1aff9e41d8ba657104f8b369402af`
- Pre-Active: `1f23a809d7274df506e03e2d9052525538c3bcf6`
- Volition: `dbc628d376515a0a523b1eecdf62129cca5d6b08`

Qualification:
- `QUALIFICATION.json`: `qualified=true`
- all four components loaded
- four local admissible cognition routes observed
- human cognition completed via `ollama:vera-local:latest`
- human result has qualified Vera host acceptance
- real Pre-Active event `c7a08dbc-0bda-4c8b-8af2-ff4b92aa5868` completed and reached `DONE`
- autonomous result has qualified Vera host acceptance
- protected-effect authority false
- native ChatGPT/OpenAI model router not replaced

Activation:
- scheduled task: `VeraDesktopRuntime-20261007-7b4b5b4`
- task readback: Enabled, At logon, exact staged launcher
- runtime install spec reconciled to requested=true / qualified=true / active=true
- staged import paths verified for Portal, Pre-Active, Vera Core, and Volition
- installer metadata reconciliation did not change frozen source bindings

Installer bookkeeping fix after live qualification:
`2bd2902f471706e526df3ab358cea59cfa3a49e5`
This fixes import verification CWD and records activation state consistently for future installs.

## Live Volition chain qualification

Evidence:
`C:\Users\patri\AppData\Local\VeraDesktopRuntime\20261007-7b4b5b4\VOLITION_CHAIN_QUALIFICATION.json`

Observed chain:
- signal event: `d81c5165-bf20-44bd-ba16-1f5a7ad0d4da`
- signal provenance: `current_observation`
- Volition adopted policy-derived `goal-0001`
- state revision: 1
- endogenous cognition event: `4d40196e-e2d6-47cf-beb9-ee226403bf5f`
- autonomous event reached `DONE`
- cognition route: `ollama:vera-local:latest`
- cognition state: COMPLETED
- acceptance: `ACCEPTED_HOST_OBSERVATION`
- Volition effect authority: false
- cognition protected-effect authority: false

## Current evidence boundary

Verified:
- four-component resident runtime is installed, live, locally qualified, and registered for logon activation.
- P.O.R.T.A.L. Desktop IPC can submit human cognition to that runtime.
- Pre-Active can autonomously produce cognition consumed through the same resident local route.
- Volition can produce an endogenous cognition request that proceeds through Pre-Active and local cognition to qualified Vera host acceptance.
- all observed cognition remains non-authoritative for protected effects.

Not claimed:
- native ChatGPT/OpenAI router interception.
- canonical-memory write from ordinary cognition acceptance.
- merge/deploy/credential/provider authority.
- consciousness or uninterrupted model-process identity.

The older `VeraUnifiedRuntime\20261006` installation remains intact as rollback evidence; this work did not destroy it or disable its activation path.

## Achilles hostile review — Draft PR #2

Review source: Project Achilles current protocol at `99bc4e8cf7bd60d022fb25222d701a8cf42f80c0`.
Reviewed P.O.R.T.A.L. subject: Draft PR #2 head `20939d548a90f36943969fd983265f99cb996164`.

Accepted findings:
- **IPC claim race:** request-file presence did not prove a request was unclaimed because the host parsed/executed before deleting the file. A timeout could therefore misclassify an already-processing request as safe to retry.
- **Caller-controlled cognition provenance:** `desktop_cognize` accepted arbitrary `source` and caller timestamps, allowing a local client to forge `PRE_ACTIVE_AUTONOMOUS_TURN` activity.

Source fixes after review:
- bridge layout now includes `bridge/claimed`;
- host atomically renames `requests/<id>.json` to `claimed/<id>.json` before parsing/execution;
- pre-existing claimed requests are replayed on host restart;
- client may report timeout-before-claim only when it successfully removes the still-unclaimed request file; otherwise outcome is unknown;
- claimed filename and payload request ID must match;
- `desktop_cognize` accepts only HUMAN source and host-stamps `created_at`;
- autonomous cognition continues to enter through the internal Pre-Active path, not IPC metadata.

Remaining explicit trust boundary:
- local IPC trusts the logged-in OS-user boundary. It does not claim cryptographic peer authentication or isolation from a hostile same-user process.

Verification after fixes:
- focused IPC/host claim tests: 9 passed;
- complete desktop source surface: 50 passed, 1 pytest-asyncio deprecation warning;
- resident modules compile;
- `git diff --check` clean.

These are source-level fixes only. The already-qualified installed runtime remains frozen at Portal `7b4b5b4...`; no new install/cutover is claimed from this review.

## Next

- Keep Draft PR #2 unmerged.
- Preserve the Achilles review as exact-head security evidence.
- Re-stage/requalify the newer hardened Portal source only under a separately authorized runtime cutover.
- Continue non-colliding source-only portfolio advancement through targeted P.O.R.T.A.L. sessions.

## Claim ceiling

`QUALIFIED_USER_LOCAL_VERA_PORTAL_PREACTIVE_VOLITION_RUNTIME_WITH_DESKTOP_IPC_AND_LOCAL_COGNITION_NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY`
