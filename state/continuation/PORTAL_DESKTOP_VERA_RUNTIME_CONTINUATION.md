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

## Trained Vera local cognition route — source-only

Patrick proposed using the locally trained Vera model for automation. Fresh host inspection established:

- live Pre-Active Qwen endpoint: `http://127.0.0.1:18081/v1`;
- currently installed endpoint is base-only and predates adapter provenance metadata;
- current served base revision: `d61dd146c8fd44c9a49cdb7f59f34e17b61902d8`;
- latest complete compatible development adapter inspected: `v10r3-lane-b-staged-sequential-step20-20261004`;
- adapter SHA-256: `b2d6eec7befca3e18cf1fa793a7830197e27bab33bea8e4f42b5a318ee91d116`;
- its training receipt's two base-shard hashes and tokenizer hash exactly match the base currently served by Pre-Active;
- current training protocol still classifies development model work as not deployed / not promoted and does not authorize deployment activation.

P.O.R.T.A.L. source now:
- probes the fixed loopback Pre-Active `/v1/models` endpoint;
- ignores endpoints that do not explicitly report `effect_authority=false`;
- treats base-only/unproven Pre-Active models as lower priority than `ollama:vera-local:latest`;
- promotes an active `vera-*` Pre-Active route to preference 5 only when both adapter SHA-256 (64 lowercase hex) and base revision (40 lowercase hex) are present;
- invokes that route through the existing OpenAI-compatible loopback `/v1/chat/completions` endpoint;
- retains Ollama invocation and fail-closed unknown-provider behavior.

Live discovery against the currently installed base-only endpoint leaves the selected route unchanged:
`ollama:vera-local:latest`.

Verification at the current desktop source head before checkpoint:
- trained-route focused tests: 10 passed;
- complete desktop source surface: 55 passed;
- `git diff --check` clean.

This is source capability only. No trained adapter was installed, bound, restarted, activated, or claimed as behaviorally promoted.

## Next

- Keep Draft PR #2 unmerged.
- Preserve the Achilles review as exact-head security evidence.
- Keep the trained Vera adapter route source-ready while training/promotion evidence matures.
- Re-stage/requalify the newer hardened Portal source only under a separately authorized runtime cutover.
- Activate a trained Vera adapter only after explicit deployment authority plus model behavioral qualification and exact host readback.
- Continue non-colliding source-only portfolio advancement through targeted P.O.R.T.A.L. sessions.

## Claim ceiling

`QUALIFIED_USER_LOCAL_VERA_PORTAL_PREACTIVE_VOLITION_RUNTIME_WITH_DESKTOP_IPC_AND_LOCAL_COGNITION_NOT_NATIVE_CHATGPT_ROUTER_NOT_PROTECTED_EFFECT_AUTHORITY`

## Resume after fresh remote update — 2026-10-07

- Fresh Portal main is `7d00d8bf3b48c0676b2278359838ee563c66c67f`; remote implementation/coordinator branches were deleted after externally performed merges. This session performed no canonical merge: the authorized local implementation branch was fast-forwarded to the fresh main to reuse completed work. The implementation branch is recreated on push.
- Exact checkpoint head is the commit containing this continuation (`git log -1 --format=%H -- state/continuation/PORTAL_DESKTOP_VERA_RUNTIME_CONTINUATION.md`).
- New passing slice: Windows pointer-sized process handles, strict finite/not-future heartbeat evidence, live PID mismatch defense, OS-locked shared startup reservation, explicit STARTING/BLOCKED/DEGRADED reporting, actionable launch failure.
- UI now connects to supervisor, starts an absent runtime, shows resident health/provenance, uses main-thread queued GUI callbacks, limits refresh concurrency, and closes only its own window. Explicit `--runtime-root` supported.
- TDD/regression: `python -m pytest -q tests/unit/test_portal_desktop_supervisor.py tests/unit/test_portal_desktop_app.py --basetemp ../test-tmp-supervisor-final` -> 45 passed. Two final interrupted tests reproduced the PID mismatch/launch failure gaps before the fixes.
- Next: finish current host responsiveness/singleton/currentness, explicit cognition policy and installer recovery contracts; run complete tests and isolated end-to-end proof. Existing runtime remains frozen. Do not trust earlier qualification as freshness evidence for this checkpoint.
- Residual strongest objection: current main's resident host performs slow cognition on the heartbeat loop, so source presence still does not establish truthful live health during inference. UI supervisor paths also require a host shim generated by installer.

## IPC durability checkpoint — 2026-10-07
- Retained request/response evidence supports identical retry without duplicate submission; request IDs bind immutable command/payload (caller timestamp excluded because host owns it).
- Atomic rename to cancelled competes with host claim. A timeout can no longer report safe cancellation after host claim wins.
- Verification: `python -m pytest -q tests/unit/test_portal_desktop_ipc.py --basetemp ../test-tmp-ipc-checkpoint` -> 10 passed; 5 new tests previously failed for missing retry/cancellation semantics.
- Exact head: commit containing this continuation. Main last freshly observed at `7d00d8bf3b48c0676b2278359838ee563c66c67f`.
- Full preliminary suite: 782 passed / 6 failed. Five failures are sandbox default TEMP permission errors; one pre-existing resume-envelope fixture expectation needs investigation. Rerun with workspace TEMP and TMP after integration.
- Next: real Pre-Active resident Engine composition, explicit route policy/currentness, portfolio-first native UI, staged installer, then integrated qualification.
- Patrick clarified product priority: producing the desktop app and its portfolio runner controls comes first; autonomous cognition remains required secondary functionality. No credential provisioning or live-runtime cutover was authorized.

## Portfolio desktop controls checkpoint — 2026-10-07
- Portfolio is now the primary native tab: profile selection, session Run/Continue/Refresh, Hold selected work, Stop, active/held/complete counts, repository work table with route and verification evidence.
- New resident control adapter reuses PortalCommandSession and its Project Runner admission/fencing; it does not introduce another scheduler. Run/Continue is bounded to one admission generation. No effect driver is installed through this profile; queued work awaits an attached worker.
- Profiles bind local wave/corpus/projects/nodes files and bounded parallelism; arbitrary worker commands/authority fields rejected. Profile values are local runtime state, not published source.
- Default desktop selection reuses an explicitly activated/qualified install when present; process liveness remains a separate supervisor check.
- Tests: `python -m pytest -q tests/unit/test_portal_desktop_app.py tests/unit/test_portal_desktop_portfolio.py --basetemp ../test-tmp-portfolio-ui-final` -> 17 passed. Real SQLite session controls tested with the external wave-preparation boundary substituted; no interactive GUI proof yet.
- Next: host worker will expose desktop_portfolio; end-to-end qualifier will verify commands through IPC and actual Pre-Active Engine cognition. Installer, policy, and resident changes are separate in-progress slices.
- Exact head is the commit containing this record. Existing credentials, logon tasks and active runtimes untouched.

## Cognition currentness and authority checkpoint — 2026-10-07
- Local no-incremental-paid cognition requires explicit policy (default false). Route expiry, cost authority and effect ceilings remain separate. Discovered Codex remains inadmissible by default.
- Loopback model transport bypasses proxies and blocks redirects. Ollama refreshes tags/show, rejects cloud-backed routes, and binds model digest/modified_at before invocation; trained Pre-Active targets retain exact model provenance.
- Results are durable before qualified intake; rejected intake retries the stored result without another model invocation. Accepted statuses are verified and source/request identity cannot drift.
- Focused suite: runtime-venv Python -m pytest cognition/local_provider/runtime/route_currentness tests -> 36 passed, including real loopback transport checks (sandbox socket restriction required approved execution).
- Exact head: commit containing this continuation. Next: resident worker checkpoint, staged installer and full qualification. Trust limit: the local model server is inside the logged-in user's host boundary; Ollama cannot atomically bind tag inspection and generation.

## Resident composition checkpoint — 2026-10-07
- Resident now composes actual QualifiedVeraRuntime, PortalCommandSession, Pre-Active Engine/Daemon/Scheduler/observers and VolitionBridge on one state-owner worker. Heartbeat/status IPC stays responsive during cognition.
- Root-specific OS lock enforces singleton across source versions. Exact SHA/tracked cleanliness/import origin checked before composition. Startup failures BLOCKED; stalled worker DEGRADED; no route leaves autonomous source PENDING without acknowledgement.
- Durable activity correlates real Pre-Active source_event_id, qualified acceptance and route provenance. Human responses return provider/model/reason/timestamps/acceptance. Portfolio controls execute through resident IPC.
- Tests: runtime-venv Python -m pytest tests/integration/test_portal_desktop_resident.py tests/unit/test_portal_desktop_host_layout.py --basetemp ../test-tmp-resident-green3 -q -> 11 passed. Real Pre-Active/Volition/Vera packages used with fake cognition; restart does not replay accepted work; responsive heartbeat, mismatch rejection, source guards and stalled worker recovery tested.
- Exact head: commit containing this continuation. Next: installer checkpoint and clean staged local-model end-to-end proof. Existing live installations untouched.

## Windows installer and recovery checkpoint — 2026-10-07
- PowerShell bootstrap binds four supplied exact SHAs into isolated user-local environment. Dry-run performs zero writes/network; local cognition policy defaults false. Full cognition qualification requires explicit local policy.
- Components instantiate before resident startup or optional OS logon registration. Matching qualified reruns preserve checkout/pip/activation/evidence and verify imported source bindings read-only. Changed source/policy requires a new root. First activation requires fresh health/source readback; failures retain actionable logs.
- Native Task Scheduler invokes the thin supervisor. Recovery can launch the installed host module when a historical shim is absent, with stdout/stderr retained in the root's logs. Desktop close still controls only its window.
- TDD: two launcher recovery/logging/CLI tests failed before implementation; final installer/install/IPC/supervisor surface -> 64 passed (including native Windows PowerShell dry-run, approved execution because sandbox denies registry reads).
- Exact head: commit containing this continuation. Next: qualification correlation proof, full tests with workspace TEMP/TMP, clean staged install and native window lifecycle probe. No logon task or live-runtime cutover performed.

## Desktop stale-health correction — 2026-10-07
- Host IPC may remain readable while process/heartbeat evidence is DEGRADED. The GUI now preserves supervisor health through that refresh; a cached ACTIVE response cannot conceal stale heartbeat evidence.
- Installer's new user-local P.O.R.T.A.L./runtimes directory joins explicit activated-install selection; explicit runtime-root remains preferred for stages.
- Focused app test observed wrong ACTIVE before fix; final app view-model suite 16 passed. Generated Python caches/packaging metadata ignored.
- Exact head: commit containing this record. Next: qualification source-event completion proof and clean staged installation.
