# VeraMonoPilot Lappy host-service receipt — 2026-10-22

**Scope:** exact USER_DIRECT authority for a single new, manually started Windows service named `VeraMonoPilot` on Lappy. This is a noncanonical, effectless Vera Mono runtime pilot. It is **not** Vera's production/cognition route. No VeraMesh service was edited.

## Frozen source and installed target

- Canonical Vera Mono source: `thebrazenbeard/vera-mono` `main@16a2828dfa7fb7e6c0e19004f014bc68fc47badf`.
- `vera_core/qualified_runtime.py` installed Git blob: `48e00014b89adeb392102ef45849fbd49fc9dc29`.
- Installed package: `D:\VERA\VeraMonoPilot\installed`, 323 measured files, SHA256 manifest digest `619eb977204fd3604be5ccd2c25082708731285c70f9dfb68335dd566d01a3b9`.
- Local Python: isolated copy of Python 3.12.10 + pywin32 312 dependencies under `D:\VERA\VeraMonoPilot\python`; installed source was packaged offline from a Git archive using `pip --no-index --no-deps --no-build-isolation --target`. **Wheel byte-for-byte build reproducibility remains HOLD** because repeated wheel SHA256 values varied across builds.
- Runtime entrypoint: `python\Lib\site-packages\vera_mono_pilot.py` (copied here as `vera_mono_pilot.py`). Uses the real `QualifiedVeraRuntime.from_state_directory(VeraStateDirectory(...))` and rejects unexpected PC/provider/source transports or trust generation.
- Config: `D:\VERA\VeraMonoPilot\config.json`, digest `fe4c669e7c090c2c674baeda0a1bc9594f1c3d959e762774518d8be516868d21`. Start policy `DEMAND`, `NO_EXTERNAL_TRANSPORTS`, provider `NONE`, source and installed hashes pinned.
- Service Control Manager service: `VeraMonoPilot`; executable `D:\VERA\VeraMonoPilot\python\pythonservice.exe`; account `NT AUTHORITY\LocalService`; start type **Manual** (no boot autostart). The only configured failure actions are restart after 2 seconds, restart after 5 seconds, then no further action, with one-hour reset. No network listener.
- ACL: pilot `python`, `installed`, `config` readable/executable by `LocalService` but not writable; pilot `state` and `run` writable by `LocalService`; SYSTEM and Administrators have full access. No ACLs changed for existing Python installation or VeraMesh services.

## Observed effects and state

1. Service installed through pywin32 `InstallService(... startType=SERVICE_DEMAND_START, userName="NT AUTHORITY\\LocalService" ...)`; SCM readback initially Stopped/Manual/LocalService.
2. First manual `Start-Service VeraMonoPilot`: SCM Running PID `31300`; heartbeat advanced and bound source, config, package hashes; checkpoint digest `83b5546e192a3250ded540bd1adc332148863301e7bc9f8cfee07aa25a7aadc3`, recovery `INITIAL`, effects `0`.
3. `Stop-Service VeraMonoPilot`: SCM Stopped, stop receipt saved; `Start-Service` again: SCM Running PID `10276`, `REOPEN`, same checkpoint digest, no provider/effect.
4. SCM failure policy configured and verified by `sc qfailure`; OS-observed PID `10276` force-terminated for this explicitly authorized test only; SCM restarted replacement PID `31212`, same checkpoint recovered, `REOPEN`, effects zero.
5. **Actual rollback:** pilot was stopped and **service registration deleted** by the exact-target `rollback_service.ps1 -Apply`. Readback showed absent service, noncanonical SQLite checkpoint still present; unrelated services not targeted. Reinstalled service with same manual LocalService image, configured crash failure actions again, manually started; SCM Running PID `24864`, `REOPEN`, identical checkpoint. This independently demonstrates removal and restoration of pilot service registration while preserving state.
6. Final checked pilot instance had no TCP connections owned by PID `24864`; service still Manual/LocalService with selected installed QVR blob and zero external effect/provider reports.

## Unresolved limitations

- This resident process only reopens **synthetic pilot state** and emits an effectless heartbeat. There is no model inference, long-horizon autonomy, tool authorization, epistemic learning demonstration or offline boot proof.
- There is **no automatic start at OS boot**. SCM restarts only after a failure of an already-started service, up to its configured limit.
- Same-host observed heartbeat, SCM status, and install manifests are not independent code-memory attestation. Transitive Python dependency byte custody, race-free import loading, provenance of copied interpreter and wheel reproducibility require further qualification.
- No merger to `vera-mono/main`, no production Vera runtime cutover, no canonical memory write, credential or provider changes.
- **Rollback:** run `D:\VERA\VeraMonoPilot\rollback_service.ps1 -Apply` elevated. It guards against wrong path/user/start mode, stops and unregisters only `VeraMonoPilot` and retains the state/package for forensic recovery. Without `-Apply` it is dry-run only.

**Evidence types:** REPOSITORY_SOURCE + INSTALLED_ARTIFACT + OWNER_RUNTIME_OBSERVATION + SCM_READBACK + EFFECTLESS_SYNTHETIC_BEHAVIOR. **Independent VN1 review remains outstanding.**
