# P.O.R.T.A.L. idle continuation gate — host-facing contract

Status: source-level coordination primitive, **not activated**, installed, or native ChatGPT interception.

## Purpose

A qualified host that receives authenticated conversation events can release one
P.O.R.T.A.L. continuation ticket 60 seconds after a completed reply if no
newer user input arrived. The gate is not a background timer, worker,
model invoker, conversation client, or effect-authority grant.

## Exact event protocol

1. Host observes a genuinely new user input with unique `input_id`:
   `record_input(input_id=...)` increments the durable input epoch.
2. Host observes the final reply for that exact input:
   `record_reply_complete(input_id=..., reply_id=..., completed_at=UTC_epoch)`.
3. A host-owned timer calls `claim_if_idle(now=UTC_epoch, claimed_by=host_id)`.
   Before the due time, this returns nothing.
4. At or after the due time, a single claimant receives `IdleTicket`.
   Its claim is durable. Other claimants fail without dispatching.
5. The owning P.O.R.T.A.L. host checks live session, currentness, routes,
   budget, authority, and existing attempt/lease state *separately*.
6. Any accepted work resumes through existing Project Runner / host pump
   admission, verification, and reconciliation—not by replaying the ticket.
7. After the initial claim, P.O.R.T.A.L. controls its own bounded refill.
   New input stops additional admission under the host's policy, not
   already-attempted effects.

## Safety and scope

- The gate has no capability to initiate a native ChatGPT UI turn.
- Plugin/skill selection is not evidence of global event coverage.
- Without a verified host event on **every** relevant new user input,
  do not claim that 60 seconds implies native ChatGPT user inactivity.
- No unpaid/paid status, execution authority, credential, or allowed target
  can be inferred from the ticket.
- A crash after ticket claim does not silently replay the same ticket.
  Reconcile the owning P.O.R.T.A.L. attempt before rerouting.
- SQLite serializes concurrent claims and records a permanent claim history.
- The host must authenticate event sources and run the timer; neither is
  implemented by this source-only gate.
- An idle ticket is only a scheduling signal. It is not Patrick's consent
  for paid compute, merges, installs, deploys, or private publication.

## Hostile review

> Objection: A perfectly implemented gate is useless if native ChatGPT never
> emits the required input and reply-completion events to the installed host.

**Accepted.** This package supplies a correct state boundary, not an
invented platform hook. Integration remains unqualified until real host
events and a no-paid P.O.R.T.A.L. execution path are independently exercised.
