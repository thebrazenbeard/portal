# PORTAL's relationship to the owner portfolio

Status: source-level relationship map; not a claim that every repository is an installed runtime dependency.
Observation: 2026-10-14, authenticated Lappy owner scan returned 90 nonarchived repositories and five archived repositories. Membership is mutable; the installed app obtains the current set itself.

## Control plane, not a generic repository launcher

PORTAL is the **portfolio control surface**. It should not copy each project's agent loop into the GUI or treat every cloned folder as executable. The architectural dependency directions are:

- Project Runner: already incorporated into PORTAL's repository source under runner/. Supplies exact-subject leases, job receipts, budgets, reconciliations and governed admission. This is an inherited in-repo kernel rather than a remote service required for every button click.
- Vera Mono: the configured resident Vera implementation lineage; only a qualified local runtime selected by the installed host can claim runtime behavior. Repository existence is not installation or model authority.
- Pre-Active + Volition: distinct resident event and drive mechanisms connected by the installed host at exact source pins. They do not imply protected-effect permission.
- Discovery: helps identify portfolio capabilities and dependencies; its census and source currentness must be treated as time-varying evidence. It is not an automatic software-dependency installer.
- Workbridge/Executor/Vera Mesh: authenticated external workstation or transport surfaces; connected devices have finite lane capacity. PORTAL needs typed, expiring, target-bound worker adapters before it may dispatch protected work through them.
- Chat Communication Bus, Intranel, Blotter and related workspaces: coordination, message integrity, activity/audit donors. Repo mentions alone do not make them active or required in the PORTAL host.
- Physica and Vera ARK: example domain workloads, not components of PORTAL's orchestration kernel. Physica currently has one explicitly bound, finite software simulation profile; ARK gameplay is not qualified by this fact.

The native Desktop presentation is **one user interface**, not the execution engine or model identity root. It should display live authenticated, nonarchived owner membership; distinguish curated registries from uncurated repos; show role hints separately from verified execution targets; and expose honest read-only qualification and locally qualified workloads.

## Current operator path

At startup, the Desktop renders the curated fallback immediately and asynchronously requests authenticated live GitHub membership. Failure leaves a clearly labeled static fallback instead of silently claiming currentness.

- **Sync GitHub** explicitly refreshes the current portfolio, including authorized private repositories, using the user's GitHub CLI.
- **Inspect main** resolves the actual default branch through authenticated GitHub API reads and verifies the exact head; it does not assume the repository is public or its default branch is named main.
- **Qualify 8 lanes** runs the bounded portfolio_readonly_batch on the exact current nonarchived membership minus the owner account-profile repository. All attempts and finals are persisted under the user's runtime state; it explicitly reports **zero project executions**.
- **Run selected** remains disabled for repositories without a reviewed, exact-source local profile. The Physica finite oscillator is the one current profile; this is not a sandbox.
- **Run curated cycle** only admits an explicitly installed, qualified runtime node manifest. Previously it silently used test fixtures; that is now forbidden. No such node manifest is manufactured by installation.
- **Vera** conversation remains separately accessible, backed by resident IPC and persisted history.

## PR reconciliation (2026-10-14)

GitHub main at beginning of work: 4089d18931eb442a23590a1ee9cc67deac339b7b.
Open Draft PR #40 is the composite of earlier #33 discovery and #34 consumer-currentness candidates, plus #35–38 host/semantic ingredients. Do not cherry-pick or merge those source changes a second time onto main when reviewing #40.

Prior Desktop branches/PR #41–43 were closed or merged into noncanonical bases and **did not put the portfolio-first source onto current main**. They were composed here on a fresh noncolliding branch; the old open PR owners were left untouched. Their historical source and tests are not independent verification of this new composition; the exact new commit must pass full local and GitHub CI, the installed GUI must render the portfolio, and the installed host must qualify from its actual source pins.

> **Hostile review — accepted:** A large portfolio list, all-green unit tests, and an eight-thread metadata scan are not a functioning cross-repository repair agent. The persistent issue #39 requires semantic mission admission, typed project worker profiles, verified repair/effects and recovery. The combined source improves the control plane and truthful UI, but it must still fail closed for unavailable worker routes. A complete claim requires real end-to-end mission/effect evidence.
