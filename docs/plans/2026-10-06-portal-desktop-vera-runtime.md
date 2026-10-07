# P.O.R.T.A.L. Desktop + Vera Runtime V1

Status: implementation in progress on `work/portal-desktop-vera-runtime-v1`.

## Product boundary

P.O.R.T.A.L. Desktop is the native human interface and lifecycle shell for a resident Vera runtime. It is not the identity root, cognition engine, or source of protected-effect authority.

```
Patrick <-> P.O.R.T.A.L. Desktop <-> local IPC <-> Vera Unified Runtime
                                            |- vera_core
                                            |- Pre-Active
                                            |- Volition
                                            |- P.O.R.T.A.L.
                                            |- cognition adapters
```

Closing the desktop window must not terminate the resident runtime. ChatGPT is optional and must not be a runtime dependency.

## Delivery order

1. Runtime supervisor: fresh heartbeat + process liveness, singleton-safe recovery.
2. Cognition discovery and authority-aware selection.
3. Resident cognition path for human and Pre-Active requests.
4. Functional desktop shell.
5. Idempotent Windows bootstrap/logon activation.
6. Qualification and hostile review.

Each coherent passing slice is committed and pushed before the next begins.

## Authority invariants

Discovery is not authorization. Model availability is not paid-compute authority. Cognition is not protected-effect authority. Source proposal is not publication authority. The UI may request exact approvals but cannot silently broaden them.

The local file bridge trusts the logged-in OS-user boundary. It does not claim cryptographic peer authentication or isolation from a hostile process already running as the same user. Within that boundary, desktop cognition provenance is host-owned: `desktop_cognize` is always recorded as `HUMAN`, its timestamp is host-stamped, and autonomous cognition enters through the internal Pre-Active path rather than caller-controlled IPC metadata.

Bridge claim state is a filesystem state transition: `requests/<id>.json` is atomically renamed to `claimed/<id>.json` before parsing/execution. A client timeout may call a request unclaimed only when it successfully removes the still-unclaimed request file; otherwise the outcome is unknown and must not be blindly retried.

## Hostile review

> **HOSTILE REVIEWER:** A GUI-owned supervisor can become a competing runtime authority if it interprets stale files as proof of life or launches a second process when the first is degraded.

**Accepted.** Supervisor state requires both a fresh heartbeat and live process for ACTIVE. A stale-but-live process is DEGRADED and is never relaunched automatically. Invalid runtime evidence is BLOCKED. The app only requests startup when the runtime is demonstrably OFFLINE.
