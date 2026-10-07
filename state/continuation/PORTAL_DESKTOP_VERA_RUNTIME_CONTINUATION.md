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
- Cognition route discovery implemented in `portal/desktop_cognition.py`.
- Ollama model inventory is discovered from the local API and typed as local/no-incremental-paid-compute/text cognition.
- Generic Codex CLI is discovered separately but is not auto-admissible because discovery does not establish provider/cost authority.
- Deterministic route selection prefers admissible local/no-incremental-paid-compute routes.
- Live Lappy probe observed Codex CLI 0.153.4 plus Ollama routes `vera-local:latest`, `qwen3:4b-instruct`, `qwen3:8b`, and `ministral-3:14b`; current selector chose `ollama:vera-local:latest`. This is runtime observation, not a permanent source guarantee.

## Verification
- `python -m pytest -q tests/unit/test_portal_desktop_supervisor.py` -> 7 passed.
- `python -m pytest -q tests/unit/test_portal_desktop_cognition.py` -> 5 passed.
- Live discovery probe selected `ollama:vera-local:latest` and left `codex:cli` non-auto-admissible.

- Resident cognition engine implemented in `portal/desktop_runtime.py` with a durable SQLite cognition ledger.
- Human and Pre-Active cognition requests use the same route-selection/invocation path.
- Completed request IDs are idempotent and are not reinvoked.
- No-admissible-route and adapter failures remain retryable rather than being falsely acknowledged.
- Every cognition record persists provider/model/route provenance and hard-codes `protected_effect_authority = 0`.
- Isolated live qualification created a real Pre-Active `autonomous.turn` event `89ec1d1c-f818-4428-aed6-f83cb75051a9`, claimed it, selected `ollama:vera-local:latest`, received exactly `LIVE_AUTONOMOUS_COGNITION_OK`, persisted evidence `cognition:0e8266416230f0ddec4d3122693f537f4fd4927775984924762efef226faf466`, and only then acknowledged the event to Pre-Active `DONE`. The proof used isolated temporary state and did not alter the installed resident runtime.

## Verification
- `python -m pytest -q tests/unit/test_portal_desktop_supervisor.py` -> 7 passed.
- `python -m pytest -q tests/unit/test_portal_desktop_cognition.py` -> 5 passed.
- `python -m pytest -q tests/unit/test_portal_desktop_runtime.py` -> 5 passed.
- Live discovery probe selected `ollama:vera-local:latest` and left `codex:cli` non-auto-admissible.
- Isolated real Pre-Active + live Ollama autonomous cognition proof completed and source event reached `DONE`.

- Desktop IPC implemented in `portal/desktop_ipc.py` using the existing unified-runtime request/response envelope.
- Resident desktop commands: status, cognition-route discovery, cognition, and recent observable activity.
- File bridge client distinguishes timeout-before-claim from claimed-but-response-missing ambiguity, preventing blind retry.
- Thin Tkinter shell implemented in `portal/desktop_app.py` and exposed as `portal-desktop`.
- The GUI owns presentation only: conversation input, runtime health, selected cognition route, autonomous/recent activity, and pending-effect display.
- Human messages are sent through `desktop_cognize`; the GUI never calls a model endpoint directly.
- The pending-effects pane currently reports no authority-bearing requests because the resident cognition path grants no protected-effect authority.

## Verification
- Desktop supervisor/cognition/runtime/IPC/app unit surfaces are green together.
- UI view-model tests verify message submission reaches IPC rather than a direct model adapter.
- Tkinter remains a presentation dependency only; tests do not require an interactive display.

## Next
Build the idempotent Windows bootstrap/runtime package, install exact source revisions into an isolated user-local environment, generate the resident host with desktop IPC + Pre-Active cognition processing, and register logon activation only after health qualification.

## Claim ceiling
SOURCE_IMPLEMENTATION_WITH_FUNCTIONAL_THIN_DESKTOP_SHELL_NOT_INSTALLED_NOT_DEPLOYED_NOT_BOUND_INTO_THE_CURRENT_INSTALLED_RESIDENT_HOST
