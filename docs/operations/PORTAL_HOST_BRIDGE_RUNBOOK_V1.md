# P.O.R.T.A.L. Host Bridge Runbook V1

Status: source-level operating procedure for the durable command-session / external-host boundary.

This runbook does not prove that P.O.R.T.A.L. is installed, resident, selected, or continuously running on any workstation or ChatGPT route. It describes how a qualified host should use the repository implementation once the relevant runtime, state database, routes, and execution tools are actually available.

## Purpose

Use this procedure when one P.O.R.T.A.L. command session is coordinating work that will be executed through a ChatGPT/plugin/workstation host rather than only through the built-in advisory process-proposal worker.

The safety rule is:

`REFRESH -> ADVERTISE -> INSPECT -> ADMIT -> BIND -> QUEUE -> TAKE/ATTEMPT-MARK -> EFFECT -> VERIFY -> RECONCILE -> REFILL`

Never infer execution authority from route discovery, connector presence, node placement, or scheduler admission.

Never replay an attempted unresolved semantic effect through a different route.

## 1. Recover before doing new work

A fresh chat or restarted host begins by reading durable state.

    portal status       --state-db .portal/portal.sqlite3       --session-id portfolio

Then inspect host effects that crossed the attempt boundary but are not terminal:

    portal host unresolved       --state-db .portal/portal.sqlite3       --session-id portfolio

If `host unresolved` returns any dispatch, reconcile that exact dispatch before attempting the same semantic work again. An `OUTCOME_UNKNOWN` record remains unresolved and route-pinned.

Queued work that has not crossed the effect boundary is visible separately:

    portal host pending       --state-db .portal/portal.sqlite3       --session-id portfolio

`pending` and `unresolved` are deliberately different. A queued dispatch may still be safely claimed by the host. An attempted unresolved dispatch may not be silently replayed.

## 2. Refresh node occupancy

P.O.R.T.A.L. can consume occupancy from three sources:

- manual snapshots: `--occupied-node NODE=COUNT`;
- local Project Runner task state: `--project-runner-tasks NODE=PATH`;
- host-observed snapshots: `--host-node-occupancy`.

A node may have only one occupancy source in a command invocation.

For a remote node observed by the ChatGPT/plugin host, publish a fresh expiring snapshot:

    portal host occupancy       --state-db .portal/portal.sqlite3       --node-id lappy       --occupied-slots 8       --ttl-seconds 300

    portal host occupancy       --state-db .portal/portal.sqlite3       --node-id worklaptop       --occupied-slots 0       --ttl-seconds 300

Inspect non-expired snapshots:

    portal host occupancy-status       --state-db .portal/portal.sqlite3

Host occupancy is monotonic currentness evidence. Older observations cannot overwrite newer observations, same-time conflicting observations fail closed, and future-dated observations are not active before their own `observed_at` timestamp.

When `--host-node-occupancy` is enabled, every enabled node not covered by a manual or local Project Runner source must have a current host snapshot. Missing or expired required snapshots stop scheduling instead of treating the node as free.

For a local Project Runner task root, use:

    portal run       --session-id portfolio       --nodes path/to/nodes.yaml       --project-runner-tasks lappy=PATH_TO_PROJECT_RUNNER_TASKS       ...

The local currentness provider reconciles process-gone/PID-reused registrations to `UNKNOWN_EXIT` before counting remaining active records. It does not kill or replay processes.

## 3. Advertise exact host routes

A route advertisement is target-bound, node-bound, capability-bound, authority-bound, expiring currentness evidence.

Example repository-native route:

    portal host advertise       --state-db .portal/portal.sqlite3       --adapter-id github       --route-id repo-native:thebrazenbeard/portal       --node-id repo-native       --target-kind repository       --target-id thebrazenbeard/portal       --capability semantic_work       --effect-capability SOURCE_ONLY       --authorized-effect SOURCE_ONLY       --preference 50       --ttl-seconds 300

Inspect current routes:

    portal host routes       --state-db .portal/portal.sqlite3

A usable route must be available, attached, current, match the assigned node and exact target, satisfy required capabilities, be technically capable of the required effect, and carry authority for that effect.

Technical capability does not create authority. Discovery does not create attachment. Availability does not create currentness.

Route observations are monotonic. Older observations cannot overwrite newer route state, same-time conflicting observations fail closed, and future-dated observations are not active before their own `observed_at` timestamp.

## 4. Admit and queue work

Run the command session with the host bridge:

    portal run       --session-id portfolio       --nodes path/to/nodes.yaml       --state-db .portal/portal.sqlite3       --host-bridge       --host-node-occupancy       --max-parallel 8

P.O.R.T.A.L. performs admission and node placement first. The host adapter then selects an exact qualified route for each admitted packet.

The session durably binds `adapter_id + route_id` before the adapter queues the host dispatch.

Inspect newly queued host work:

    portal host pending       --state-db .portal/portal.sqlite3       --session-id portfolio

Do not execute a host effect merely because the dispatch is present. The exact dispatch envelope is the work-bearing record.

## 5. Take and mark the effect boundary before calling the external route

For an interactive ChatGPT/plugin host, prefer the atomic take operation. Supply only adapter IDs the current host can actually drive:

    portal host take       --state-db .portal/portal.sqlite3       --session-id portfolio       --adapter-id github       --adapter-id workbridge       --attempt-id HOST_UNIQUE_ATTEMPT_ID       --evidence-id HOST_REQUEST_EVIDENCE_ID

`host take` selects the oldest queued dispatch among those adapter IDs whose exact bound route is still qualified and marks it `ATTEMPTED` in the same transaction before returning the work-bearing envelope. Route qualification is revalidated at this effect boundary: exact target/node, availability, attachment, currentness window, required capability, technical effect capability, and effect authority must still hold. A stale or no-longer-authorized queued dispatch is not attempted merely because it was valid when queued.

If the host already selected one exact dispatch through another race-safe mechanism, the lower-level equivalent remains:

    portal host attempt       --state-db .portal/portal.sqlite3       --dispatch-id DISPATCH_ID       --attempt-id HOST_UNIQUE_ATTEMPT_ID       --evidence-id HOST_REQUEST_EVIDENCE_ID

Only after the durable attempt marker succeeds should the host invoke the external effect. Exact replay of the same already-recorded attempt is idempotent; a different second attempt is refused after the boundary.

If the host disconnects, times out, crashes, loses the response, or receives an ambiguous result after the attempt marker, the effect is unresolved. Do not retry it through GitHub, WorkBridge, Executor, Lappy V2, or another route until the original effect is reconciled.

Embedded hosts may use the public `PortalHostPump` API. The pump operates on already-bound work only: it persists the attempt before calling the bound driver, records owning-driver reconciliation evidence, leaves driver exceptions unresolved rather than replaying them, treats a competing host winning the attempt race as a non-effecting `RACE_LOST`, and does not swallow process-control exceptions.

## 6. Verify through the owning substrate

After the effect call, verify the result through the substrate that owns the effect.

Examples of verification evidence include:

- exact Git commit/ref readback for a repository mutation;
- exact Project Runner/task receipt for workstation process work;
- exact provider/service receipt for a service-native effect;
- explicit no-effect/held evidence when the work did not produce the intended mutation.

Do not turn worker success, a returned string, or an internal assurance statement into `VERIFIED_COMPLETE` without owning-substrate evidence.

## 7. Reconcile the exact dispatch

Record the owning-host result:

    portal host reconcile       --state-db .portal/portal.sqlite3       --dispatch-id DISPATCH_ID       --state VERIFIED_COMPLETE       --evidence-id OWNED_VERIFICATION_EVIDENCE

Allowed reconciliation states are:

- `IN_PROGRESS`
- `OUTCOME_UNKNOWN`
- `VERIFIED_COMPLETE`
- `VERIFIED_HELD`

`OUTCOME_UNKNOWN` remains active and route-pinned. It does not free capacity.

`VERIFIED_COMPLETE` moves the subject terminal. `VERIFIED_HELD` moves it held. Both are durable.

## 8. Refill

After reconciliation:

    portal continue       --session-id portfolio       --nodes path/to/nodes.yaml       --state-db .portal/portal.sqlite3       --host-bridge       --host-node-occupancy       --verifier vera-review

P.O.R.T.A.L. reconciles the owning adapter first, recomputes active/excluded subjects and live node occupancy, then admits the next collision-safe work into freed capacity.

For a resident run, `portal run --session-id ...` performs reconcile/refill generations by default. The source loop supports `max_cycles=0` and `max_idle_cycles=0` for polling until STOP or verified idle; use `--once` for one generation. Source-level integration coverage verifies the closed loop through host queue, durable attempt, owning-driver verification, capacity release, next-subject refill and final verified idle.

## 9. Hold, stop, and complete

Hold records durable no-refill intent:

    portal hold       --state-db .portal/portal.sqlite3       --session-id portfolio       --subject-id SUBJECT

Already-dispatched work continues to occupy capacity until verified held or terminal evidence exists. Hold is not cancellation authority.

Stop prevents new admission/refill without erasing active or unresolved effects:

    portal stop       --state-db .portal/portal.sqlite3       --session-id portfolio

Route-bound completion must go through the owning adapter:

    portal complete       --state-db .portal/portal.sqlite3       --session-id portfolio       --subject-id SUBJECT       --verifier vera-review       --host-bridge

A route-bound subject cannot fall back to the legacy wave verifier merely because the host adapter is unavailable.

## 10. Fresh-chat checklist

Before a new P.O.R.T.A.L. chat advances work:

1. read `portal status`;
2. read `portal host unresolved`;
3. reconcile unresolved attempted effects before any semantic retry;
4. refresh current node occupancy;
5. refresh exact target-bound route advertisements from live host capabilities and authority;
6. inspect `portal host pending` and select only adapter IDs the current host can actually drive;
7. prefer `portal host take` so route qualification is revalidated and the attempt is durably recorded atomically;
8. execute only the exact returned/bound dispatch through that route;
9. verify through the owning substrate;
10. reconcile and refill.

If any required currentness, route attachment, capability, authority, or effect outcome is unknown, fail closed for that subject and continue unrelated safe work where possible.

## Protected-effect boundary

P.O.R.T.A.L. scheduling does not itself grant authority to merge, mutate protected branches, deploy, install/activate, change credentials or permissions, spend money, publish private material, perform destructive state changes, or take any other protected effect.

`SCHEDULABLE != AUTHORIZED`

`BOUND != ATTEMPTED`

`ATTEMPTED != VERIFIED`

`OUTCOME_UNKNOWN != SAFE_TO_RETRY`

`SOURCE_PRESENT != INSTALLED_OR_RUNNING`
