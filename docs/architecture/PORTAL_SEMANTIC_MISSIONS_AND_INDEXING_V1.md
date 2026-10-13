# Semantic missions, governed execution, and measured scheduling (V1)

Status: proposed architecture with one implemented CLI bridge; not a qualified autonomous mission runner.

## The actual product goal

One operator sentence should become a durable scoped mission, not sixty independent prompts.
Example request: "Make Project Fukital and its required repositories function together on Lappy."
The correct acceptance test is integrated host behavior, not a list of green source PRs.

A mission is a first-class typed object with:
- Exact operator objective, accepted interpretation, unknowns and operator provenance.
- Exact target identities: repositories, dependencies, host, permitted environments and exclusions.
- Success probes fixed before execution, integration/rollback requirements and failure ceilings.
- Source, runtime, deployment and protected-effect authority separately represented.
- Current discovered frontier; accepted/rejected alternatives; blocked and delegated subjects.
- Cost, iteration and stop budgets; run identity, replay fences and continuation checkpoints.
- Every effect bound to an independently rechecked exact target, authorization and result.

No indefinite "fix everything" inference. If Fukital is ambiguous, retain an UNBOUND
mission and resolve its exact scope before initiating write or deployment effects.

## Semantics and execution are different layers

1. SEMANTIC INTERPRETATION: derive candidate work goals, issue identity, relevance,
   causal dependencies, scope and tests from evidence. Reject invented certainty.
2. FRONTIER GENERATION: detect source errors, missing interfaces, runtime failures,
   stale PRs and integration defects. Keep directly observed facts separate from hypotheses.
3. SCHEDULING: choose runnable, non-colliding, highest-value work, including abstention.
4. EXECUTION: use existing Project Runner claims, fences, budgets and qualified drivers.
5. VERIFICATION: independent readback, regression checks, installation/route/process/
   behavior evidence and failure reconciliation. Failed verification creates a new frontier.
6. REASSESSMENT: reconsider the actual goal and stop when qualified, blocked or budgeted out.

The dependency graph is not the queue. DAG/topology answers "what must precede what";
the work index answers "which eligible item is next". Blotter provides observations,
not execution commands. No source test may be promoted into installed-runtime success.

## Why finger trees might, and might not, belong

A measured finger tree can represent an ordered collection with cached associative
summary measures, allowing efficient persistent insertion/splitting and prefix search.
An eligible measure might aggregate stable work metadata such as item count and minimum
priority. It must never cache transient route authority, live occupancy, wall-clock freshness,
trust or merged source truth. Those are revalidated by existing gates at attempt time.

Existing Project Runner already sorts small deterministic advancement waves and uses
SQLite to persist exact claims. A finger tree **does not** improve semantics, solve
effect ambiguity or manufacture autonomy. Without measured queue-growth bottlenecks,
it adds correctness risk and synchronization overhead. Start with current sorting
and SQLite indexes. Benchmark representative 50, 500, and 5000 item frontiers with
work changes, priorities, occupied lanes, and collision/reconciliation churn.
Only consider replacing the in-memory index after a held-out benchmark shows an
end-to-end advantage, with byte-for-byte identical admission/deferral decisions.

## First implemented slice: reduced operator steering

`portal advance --session-id portfolio --state-db <durable-db>` validates that the
specific session and saved configuration exist and delegates exactly one turn to
`portal continue --resume` in the existing guarded execution path.
`--dry-run` inspects without dispatch. `--continuous` reuses the saved
`portal run --resume` loop explicitly, without claiming off-turn ChatGPT continuity.
Only a small, exact set of portfolio intent phrasings are recognized; a new named
project goal fails closed instead of accidentally executing the unrelated portfolio.
Configured adapters, quotas, scopes and authority are not expanded by the alias.

This is deliberately a *bridge*, not the full one-sentence Fukital mission feature.
It removes repeated configuration for an existing session; it does not discover
Fukital, identify every issue, author fixes, install a runtime, or certify the system.

## Next implementation increments

1. `MissionSpec` plus immutable acceptance contract and typed scoped intake.
2. Native project/issue/repository and host-environment readers, with coverage evidence,
   authentication awareness, and explicit handling for private or unreachable sources.
3. Semantic issue deduplication, dependency DAG, risk-based frontier and repair proposals.
4. Controlled repair/review/test loop, with non-colliding lanes and owner constraints.
5. Verified staged installation on the named host, canary and rollback.
6. Host-level behavior/effect acceptance tests with exact receipt and cold restart proof.
7. Automatic, budgeted reappraisal until qualified stop, genuine blocker or operator hold.

Do not build a second scheduler, trust store, memory authority or worker protocol
if Portal/Project Runner/Vera Mono already supplies a qualified mechanism.

## Internal hostile review

> **Objection:** A natural-language command is not a trustworthy permission grant.
> An LLM may confuse compatible code with an installable production system.

**Accepted.** Compile semantics into exact typed mission targets. Preserve separate
scope, qualification and authority checks. Revalidate immediately before effects;
missing scope or outcome tests means defer, not "complete". A sentence authorizes
investigation of its declared target, not unspecified production changes.

> **Objection:** The CLI convenience alias still requires a previously configured
> session and is not the one-command cross-repository closed loop requested.

**Accepted.** Report it as a verified first slice only. The durable mission-intake
and rediscovery mechanism remains the principal missing engineering subject.

> **Objection:** Finger trees are algorithmic theater while the actual throughput
> problem is missing driver integration, stale host-route observations or no
> resident process.

**Accepted.** First prove functional host routing and automatic refill. Benchmark
and use a tree only when a demonstrated queue bottleneck survives profiling.

## Qualification gates

- A reference mission with at least two interacting repositories starts from one
  statement, derives correct dependencies and declines an ambiguous target.
- With seeded defects, runs reproducible red-to-green repair and integration probes.
- Blocked/unauthorized/stale/delegated work stays held while unrelated work advances.
- Crash/restart preserves progress and ambiguous effects without replay.
- A staged Lappy install reports exact package, live selected route, process
  consumption and behavior, with successful rollback and negative tests.
- A separate independent reviewer checks the exact frozen mission/evidence cut.
- Never claim "all issues fixed"; claim only the enumerated scope and observed probes.

Current source change achieves only the CLI bridge and related source tests.
It is not installed, activated, merged, externally reviewed or end-to-end qualified.
