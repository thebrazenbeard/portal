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

## Next
Wire human and Pre-Active cognition requests through the resident runtime, invoke the selected Ollama route, persist provenance, and keep unresolved requests retryable when no route is admissible.

## Claim ceiling
SOURCE_IMPLEMENTATION_CHECKPOINT_ONLY_NOT_INSTALLED_DESKTOP_NOT_DEPLOYED_COGNITION_DISCOVERY_AND_SELECTION_SOURCE_BEHAVIOR_ONLY
