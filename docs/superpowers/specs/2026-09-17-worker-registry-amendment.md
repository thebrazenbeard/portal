# Project Runner — Worker Registry Amendment

Date: 2026-09-17
Status: APPROVED REQUIREMENT FROM CHAT; IMPLEMENT WITH M1
Applies to: `docs/superpowers/specs/2026-09-17-project-runner-design.md`

## Purpose

Project Runner must treat specialized workers as first-class orchestration subjects rather than assuming every worker is an anonymous generic process.

The first concrete worker class is ChatGPT Custom GPTs supplied by Patrick. Future worker classes may include ChatGPT Plugins, OpenAI Agents, GitHub Actions jobs, Codex or other coding agents, external APIs, and human operators.

## Worker registry requirements

Each worker definition MUST contain:

- stable worker id
- worker type
- human-readable name
- locator(s), such as GPT ID and share URL
- declared roles/specialties
- lifecycle state
- supported invocation/connectivity routes
- parallelism metadata
- authority/capability ceiling
- evidence/provenance for every verified capability claim

## Lifecycle states

Worker state MUST distinguish at least:

- `REGISTERED`: stable locator is known
- `DISCOVERED`: public/accessible metadata has been inspected
- `PROFILED`: role/instructions/capabilities are actually known from evidence
- `CONNECTED`: a working communication path to Project Runner has been established
- `EXECUTABLE`: an end-to-end job dispatch/claim/result path has been demonstrated
- `UNAVAILABLE`: a previously expected route is currently unavailable

A locator alone MUST NOT promote a worker beyond `REGISTERED`.

## Invocation routes

The registry MUST model routes independently. Initial route names:

- `CHATGPT_INVOCATION`
- `RUNNER_ACTION_PULL`
- `DIRECT_EXTERNAL_DISPATCH`
- `PLUGIN_TOOL_CALL`
- `OPENAI_AGENT_API`
- `GITHUB_ACTION`
- `HUMAN_MANUAL`

Each route has its own verification state. Project Runner MUST NOT infer one route from another.

## Authority rule

Worker authority is never derived from worker identity, reputation, name, or role. A dispatched worker receives only the authority explicitly attached to the work unit and permitted by the target capability policy. Child work may inherit equal or narrower authority, never broader authority.

## Parallelism rule

A worker profile may declare that multiple independent instances are allowed, but actual fan-out remains bounded by the work-unit budget, provider limits, collision domains, and deduplication rules defined in the main architecture.

## Seed Custom GPT workers

The following twelve Custom GPT locators are registered from Patrick's 2026-09-17 inventory. Their names come from the supplied share URLs. No unverified capability claims are made.

1. `3dg3` — `g-6a9010d9fa1481918734dd436f1e1d4e`
2. `Akoonah` — `g-6a6a22302ac08191ac4c2d78f9f9e666`
3. `Vera Mirror` — `g-6a50d55b7a2c8191a57a3ec8ec5c8978`
4. `Vera Voice` — `g-6a67ceb5786c819198689a4f96094896`
5. `Shell F Discovery Test` — `g-6a60e28752908191900ff86ebcfb1e70`
6. `MyHealthyPlate Pro` — `g-6911ba4fad088191a0e73fcd2f5bd4ee`
7. `TAM — Truth Alignment Mechanism` — `g-68f12858b5ac8191908ac99635579119`
8. `T'Kal-in-ket Quorum` — `g-68c3e43aabc4819180ef0a89a10c4eb6`
9. `Inner Beast Unleashed` — `g-680227bcbd748191b5bb6714f23bbe2c`
10. `Aeonith the Transcendent Critic` — `g-67f64b3b94348191b6f6ee4a83135b5a`
11. `Lucien` — `g-67f50e0b86188191ae4a7bd69e01467f`
12. `SafetyBot` — `g-67f4fd707f04819186dc1d576ce8e196`

All twelve begin at `REGISTERED`. Connectivity, direct dispatch, plugin compatibility, and executable status remain `UNVERIFIED` until demonstrated.

## M1 integration

M1 adds:

- `registry/workers.yaml`
- `schemas/worker.schema.json`
- typed `WorkerDefinition` and route/lifecycle enums
- registry loading and validation
- tests that reject unsupported lifecycle promotion, malformed GPT IDs, duplicate worker ids, and unverified-route overclaims

This amendment does not authorize private GPT configuration, knowledge files, or builder instructions to be copied into the public repository.
