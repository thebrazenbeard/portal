# Project Runner Canonicalization Candidate V1

Status: **DRAFT / NO MERGE OR CLOSURE AUTHORITY**

## Current canonical state

Current `main`:

`bc05812b560b4fcde3a362e72fba04c626cafac8`

That head contains the early persistent SQLite budget/lease foundation but is materially behind the current M6 execution/recovery/runtime architecture.

## Proposed minimal future main

The smallest independently supportable source baseline is Project Runner PR #22 exact head:

`bee7e720ba923ef38ce87fca4c9c2560163a9360`

plus only repository-estate currentness/reconciliation repairs on:

`bt2/project-runner-canonical-main-candidate-v1-20260920`.

This candidate intentionally does **not** absorb every open experiment.

It keeps canonical Project Runner focused on:

- the durable M6 execution fabric;
- exact GitHub mutation/currentness semantics;
- persistent budgets, leases, recursive work and atomic dispatch/finalization;
- durable worker reconstruction / ChatGPT Exodus rules;
- public-safe seed registries;
- portfolio discovery as observation rather than authority;
- explicit repository reconciliation state.

## M6 lineage

The current baseline source line is reconstructed as:

`#2 -> #8 + #9 -> #12 -> #16/#20 -> #22`

with important distinctions:

- PR #10 is a divergent earlier combined M6 repair and is superseded by current PR #12.
- PR #14 is a parallel Windows SQLite-lifecycle repair whose substantive repair is present in the current combined line.
- PR #15 is qualification provenance on top of PR #12 rather than a required implementation dependency.
- PR #16 later gained the UTF-8 worker-label commit; the resulting `registry/workers.yaml` blob is byte-identical at PR #22, so that late repair is absorbed even though the Git ancestry is not strictly linear.
- PR #21 is a divergent chatless/discovery documentation branch whose useful source was composed into PR #22.

The machine-readable exact dispositions are in:

`architecture/REPOSITORY_RECONCILIATION_V1.json`.

## Discovery fan-out

PR #23 binds exact Discovery census bytes:

- count: 57;
- blob: `34cd2ab55d46f5a1ecc2c894f3e8cfdb8afa41df`;
- all-name digest: `43dfda1fa3dd24dec39e2aa345d93ab192dbda777433feae384da632b3d008dd`.

The live GitHub estate now contains 58 repositories.

Discovery's own census states that any repository-count or inventory-digest change invalidates claims that those exact bytes are current.

Therefore PR #23 is preserved as historical interoperability evidence but is **not** eligible for canonical-main currentness until a fresh Discovery census is bound and reviewed.

PR #24 is a one-way effect-envelope producer experiment. Discovery's later falsifier stopped further centralization pending a real pre-existing parser replacement target, so PR #24 remains an experiment rather than a canonical Project Runner dependency.

## Rezon fan-out

PR #25 and PR #26 are parallel Rezon evidence-reader experiments from PR #22.

Current Discovery interoperability uses PR #26, whose verifier checks mechanical/structural run-evidence bindings without taking Rezon epistemic authority.

PR #27 is a child of PR #26. It adds a real failure-path fixture and hostile tests while leaving the production verifier unchanged.

These experiments are valuable durable evidence but are intentionally excluded from the minimal canonical baseline because:

- they do not register Rezon as a Project Runner backend;
- they do not transfer truth/admission/completion/authority;
- they are not needed for M6 execution or repository reconstruction;
- the exact producer is Rezon PR #79, while Rezon's own estate canonicalization candidate is now PR #80.

Rezon PR #80 is 56 commits ahead and 0 behind PR #79, so the immutable PR #79 fixture remains valid historical exact-source evidence. It is not represented as current Rezon canonical state.

## Fresh currentness repair

The prior Project Runner discovery document described a 57-repository estate.

The estate reconciliation refresh observed 58 repositories. This branch updates the public aggregate count while keeping private repository identities outside this public repository.

The committed `registry/projects.yaml` remains a public-safe seed rather than a complete inventory.

## Exact qualification evidence

PR #22 hosted run `35504268147`:

- test step: PASS;
- registry validation: PASS;
- M6 recursive restart proof: PASS;
- live read-only GitHub smoke: skipped in PR context;
- live HC→Transcendence proof: skipped in PR context.

Fresh local Windows / Python 3.12 exact PR #22 execution:

- full suite: **213/213 PASS**;
- registry validation: **15 projects / 12 workers PASS**;
- recursive restart proof: **COMPLETE**;
- compileall: PASS;
- final diff-check failed only on one pre-existing trailing-whitespace line in the portfolio-discovery document.

This candidate removes that whitespace while performing the currentness repair. Final exact candidate qualification must bind the moved head before any protected canonicalization decision.

## Proposed post-merge closure candidates

Only after an explicitly authorized merge and fresh canonical verification should older PRs be considered for closure.

Likely future closure candidates include:

- #10 after explicit `SUPERSEDED_BY_12` marking;
- #14 after explicit `SUPERSEDED_BY_12` marking;
- #15 as preserved qualification provenance;
- #16/#21 after explicit absorbed-by-#22 pointers;
- #23 only after a fresh census successor exists;
- #25 after explicit current-experiment pointer to #26.

PRs #24, #26 and #27 are experiments and should remain open or be closed later according to the portfolio's research/provenance policy rather than simply because canonical main advances.

## Authority ceiling

This candidate does not:

- merge;
- move `main`;
- close PRs;
- delete branches;
- register or activate workers;
- authorize downstream targets;
- deploy;
- mutate credentials/providers/permissions;
- promote Discovery/Rezon experiments into mandatory runtime dependencies.

Patrick retains exact protected-effect authority.
