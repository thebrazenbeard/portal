# Project Runner Worker Reconstruction V1

Status: CANDIDATE / CHATGPT EXODUS DECHATIFICATION

Project Runner is a durable orchestration kernel and portfolio execution role. It is not a ChatGPT conversation, browser tab, model session, or permanent worker chat. A future runtime may instantiate Project Runner temporarily, perform bounded work, persist evidence, and terminate without losing the worker or portfolio state.

## Persistent interface ownership

The intended persistent ChatGPT interfaces are exactly:

- `Vera` — Patrick-facing cross-project reasoning and delegation;
- `Vera Control Plane Coordinator` — Vera runtime/provider/control-plane coordination;
- `BT2 Coordinator` — primary engineering/portfolio interface for Project Runner dispatch and worker orchestration.

Project Runner requires no fourth permanent chat. No worker dispatched by Project Runner requires a permanent chat merely because a prior execution used one.

## Reconstruction order

A fresh Project Runner terminal must reconstruct from durable evidence rather than transcript memory:

1. fresh-read this repository `main`, open PRs, current exact heads, reviews, workflows, and the Project Runner operating contract;
2. load the applicable project registry: the committed public-safe seed or a complete external private registry whose exact SHA-256 is verified;
3. fresh-read the current Chat Communication Bus topology and applicable assignments/handoffs;
4. read the latest applicable Project Runner portfolio continuation/checkpoint in `thebrazenbeard/vera-control-plane` as a starting snapshot only;
5. fresh-check every mutable downstream subject named by that checkpoint before carrying status forward;
6. read provider/install/runtime state only when the active frontier depends on it;
7. verify current exact authority before any protected effect.

A checkpoint timestamp or newer-looking branch does not win by itself. If two durable continuations disagree, preserve the conflict and reconcile from exact current evidence.

## Worker records and locators

`registry/workers.yaml` contains registered endpoint/worker records. A ChatGPT Custom GPT share URL or GPT ID is a locator, not durable authority, currentness, assignment, memory, or successful dispatch evidence. Routes explicitly marked `UNVERIFIED` remain unusable as proof of an executable path. The Project Runner reference read worker is different: its durable reconstruction path points to `runner/reference_worker.py`, its route ceiling is READ_ONLY/SAFE, and its VERIFIED state is qualified by the repository's live reference-worker workflow proof rather than by a Custom GPT locator.

Named workers such as Radar, One, Two, Three, Four, Hephaestus, Parallax, Noah, reviewers, and project-specific workers derive their identity, routing, scope, and authority from their own durable repository/Bus contracts and current assignments. Project Runner must not reconstruct them from a remembered chat personality or a conversation URL.

Execution-lane labels are not automatically durable identities. A label such as a verifier lane, hostile-review lane, or project-specific execution lane is treated as a bounded runtime role unless current source explicitly establishes a durable logical identity.

## Minimum dispatch state

Before Project Runner dispatches a worker, durable state must establish:

- target project/repository and exact work subject;
- worker/role and current route or invocation mechanism;
- required capabilities and collision domain;
- current dependency/frontier state;
- exact authority ceiling and prohibited effects;
- where results/reviews/receipts must be persisted;
- post-work verification/currentness requirements.

If any of those exist only in an archived conversation, the work is `WORKER_RECONSTRUCTION_GAP` and is not READY.

## Chat dependency prohibition

A `chatgpt.com` conversation URL, title, conversation ID, or instruction such as `ask the main chat` may be retained as historical provenance. It must not be the only locator for current state, assignment, identity, authority, or recovery.

A successful Project Runner completion must leave enough durable GitHub/Bus evidence that destroying access to the terminal does not materially reduce the ability to reproduce, audit, challenge, or continue the work.

## Communication and persistence

Non-PR work-bearing communication uses `thebrazenbeard/chat-communication-bus` under its current protocol/topology. Source PRs remain canonical in source repositories and are mirrored/referenced through the Bus as required.

Project Runner durable execution state belongs in its own stores/receipts; cross-portfolio continuation state may be persisted in the established `vera-control-plane/state/continuation` mechanism. A chat transcript is never the completion artifact.

## Authority and claim ceilings

Project Runner technical capability does not create target authority. Registry presence, worker assignment, a successful test, a review, a Bus message, a checkpoint, or a backend token does not authorize merge, deployment, install, provider mutation, credentials/permissions, training, publication, canonical-memory/canon mutation, deletion, or another protected effect.

Fresh exact authority and exact predecessor/readback requirements remain mandatory.

## Reconstruction test

This worker is dechatified only when a fresh `BT2 Coordinator`, `Vera`, or `Vera Control Plane Coordinator` runtime can determine current portfolio state, active workers, completed evidence, failures, unresolved frontiers, authority, and the next safe action from GitHub/Bus/provider evidence without opening a retired Project Runner conversation.
