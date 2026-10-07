# P.O.R.T.A.L. Operational Qualification — 2026-10-06 15:08 ET

Status: BOUNDED NON-PROTECTED OPERATIONAL EVIDENCE

Qualification session: `portal-live-qual`
Working branch: `work/portal-coordinator-v1`
Bound B subject: `portal-qual-b`
Bound B dispatch: `93c30e892e781fada8663105b24aacea1ee688f5a599ff2621ddd4b54ea7a419`
Bound B attempt: `portal-live-qual-b-attempt-1`
Bound adapter: `github`
Bound route: `repo-native:thebrazenbeard/portal`
Bound pre-attempt head: `2a851a989dbe26589966a450df04a62c50523da5`
Effect ceiling: `SOURCE_ONLY`

## Live route/currentness observations

- WorkBridge Commander was attached to Lappy and exposed local file/process capability.
- Lappy Desktop Commander V2 reported an authenticated, data-plane-verified route with fs/process capabilities.
- WorkBridge Relay reported read/write/process disabled.
- Executor reported no connected devices.
- Only the exact GitHub repository route was advertised into this qualification session. Discovery did not manufacture effect authority.
- Project Runner's live monitor observed three CODEX_BRIDGE/STRONG app-server processes on Lappy. They were treated as occupied/identity-unverified work and were not killed, replayed, or reassigned.
- Qualification occupancy was published as `lappy=3`, `repo-native=0`.

## Closed-loop A evidence

At `ceed3b638bc7bedbd3c3200fb255ecda565b1f9d`, P.O.R.T.A.L. admitted `portal-qual-a`, durably bound the exact GitHub route, queued dispatch
`ca4d208fedb2beadc52da32e0698d3f4793342d7b5e510aead07580931a90981`,
and atomically marked attempt `portal-live-qual-a-attempt-1` before the external effect.

The bound GitHub effect created:
`state/qualification/PORTAL_OPERATIONAL_QUALIFICATION_20261006T1503-0400_A.json`

Owning-substrate readback verified commit:
`2a851a989dbe26589966a450df04a62c50523da5`

P.O.R.T.A.L. then reconciled A as `VERIFIED_COMPLETE`. The session projection moved A to terminal and admitted `portal-qual-b` into the freed repo-native slot.

## Fail-closed route evidence

While B was queued and GitHub-bound, `host take --adapter-id executor` returned `dispatch: null`.
The B dispatch remained queued, unattempted, and bound to the GitHub route. No route substitution occurred.

## Stale/PID-reused task evidence

A disposable Project Runner task root contained three records:
- a dead PID;
- a live PID with intentionally mismatched process-start identity;
- a live PID with identity evidence deliberately left unverifiable.

`LocalProjectRunnerTaskCurrentness.snapshot()` reconciled exactly two records to `UNKNOWN_EXIT`:
- `PROCESS_GONE_WITHOUT_FINAL_RECEIPT`
- `PROCESS_IDENTITY_REUSED_WITHOUT_FINAL_RECEIPT`

The identity-unverified live record remained active and consumed one occupied slot.
No process was killed or replayed.

## OUTCOME_UNKNOWN recovery evidence

A separate durable SQLite qualification database crossed the attempt boundary on an exact GitHub-bound dispatch and was reconciled to `OUTCOME_UNKNOWN`. A later fresh process read the database and observed:
- subject state `ACTIVE`;
- verification state `OUTCOME_UNKNOWN`;
- `reconciliation_required = 1`;
- original adapter `github`;
- original route `repo-native:thebrazenbeard/portal`;
- no pending replay.

A subsequent `host take --adapter-id workbridge` returned `dispatch: null`.
The ambiguous attempted effect therefore survived the process boundary and remained route-pinned.

## Source/CI evidence

Exact-head targeted host/session/recovery regression at `ceed3b638bc7bedbd3c3200fb255ecda565b1f9d`:
`6 passed`.

GitHub Actions at `2a851a989dbe26589966a450df04a62c50523da5`:
- `test`: SUCCESS
- `Dependency Review`: SUCCESS

## Claim ceiling

This evidence demonstrates bounded operational consumption of P.O.R.T.A.L.'s durable coordination/host-bridge path and one real non-protected GitHub branch effect with owning-substrate readback.

It does not establish deployment, installation as a resident service, continuous between-turn operation, production-provider mutation, protected-effect authority, or permission to merge PR #1.

Automatic resident refill remains a distinct qualification dimension until observed through a resident host loop rather than inferred from source tests alone.
