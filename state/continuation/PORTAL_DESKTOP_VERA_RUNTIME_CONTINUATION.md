# P.O.R.T.A.L. Desktop + Vera Runtime continuation

Branch: `work/portal-desktop-vera-runtime-v1`
Base at start: `a4cf02d34c2f8c4ac2b1e60161cc53e9845616f0`

## Completed
- Product/authority architecture recorded.
- Runtime supervisor implemented in `portal/desktop_supervisor.py`.
- Runtime state vocabulary: OFFLINE, STARTING, ACTIVE, DEGRADED, BLOCKED.
- ACTIVE requires live PID, fresh heartbeat, and all four runtime components loaded.
- Stale heartbeat + live process is DEGRADED and is not relaunched.
- Invalid heartbeat is BLOCKED and is not relaunched.
- OFFLINE runtime can be started through an injected/native launcher.

## Verification
- `python -m pytest -q tests/unit/test_portal_desktop_supervisor.py` -> 7 passed.

## Next
Implement cognition route discovery and deterministic authority-aware selection. Re-probe local Ollama/Codex at runtime; do not hard-code prior observations as guaranteed availability.

## Claim ceiling
SOURCE_IMPLEMENTATION_CHECKPOINT_ONLY_NOT_INSTALLED_DESKTOP_NOT_DEPLOYED_NOT_RUNTIME_ROUTE_SELECTION
