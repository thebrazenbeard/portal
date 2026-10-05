# Build Team Two Portfolio Status — 2026-09-28

Observed checkpoint: 2026-09-28, America/Indiana/Indianapolis.

## Estate

Live GitHub estate:

- 71 repositories total
- 53 public
- 18 private
- 2 archived, both private

The four public additions relative to the 67-repository cut were:

- `thebrazenbeard/axle`
- `thebrazenbeard/ingest`
- `thebrazenbeard/lgcm`
- `thebrazenbeard/pro-run`

The public P0 repository set remains ten subjects.

## Canonical main work already landed

### Project Runner

Canonical main before this status checkpoint:

`b831f4665300769a72af21b30d099568d5fdb280`

Landed in dependency order:

1. live 71-repository / 53-public corpus;
2. 55-subject public advancement wave: 53 repositories + 2 public workstreams;
3. bounded admission/scheduling layer;
4. execution spine:
   - wave -> Operator binding;
   - durable claim/fence path;
   - exact current-head recheck;
   - independent review and authority gates;
   - promotion-bound GitHub SOURCE_WRITE adapter;
   - read-only SOURCE_WRITE route qualification;
   - OUTCOME_UNKNOWN reconciliation;
   - EFFECT_CONFIRMED finalization without backend replay.

Windows/Lappy found a cross-platform defect while composing the execution spine:
raw CRLF working-tree bytes were being compared directly to a Git blob binding.
The repaired implementation computes the path-aware Git blob under Git filters
when inside a checkout and uses raw blob hashing only as the fallback outside Git.

Exact local qualification on the composed execution-spine tree:

- full pytest: 319 passed;
- focused execution/source-write suite: 65 passed;
- registry validation: PASS;
- compileall: PASS;
- git diff --check: PASS.

GitHub exact-head PR qualification also passed:

- Project Runner PR #49 dependency review: PASS;
- Project Runner PR #49 test workflow: PASS.

PR #49 was merged to main as
`b831f4665300769a72af21b30d099568d5fdb280`.

### Pro-Run

Canonical main:

`a770e19adc104a14eebcbaa5c012cec01f4e140e`

Landed:

- stale run-error recovery;
- Windows/Lappy runtime package;
- local Qwen HTTP/tool-call layer;
- watchdog/task/runtime launch substrate.

The Windows/Qwen successor was restacked after the recovery fix and qualified
before merge.

### Ingest

Canonical main:

`64623422834229ed8665ed66308fd69ab55b9da0`

PR #7 is landed. It adds:

- descriptor-bound local-file acquisition;
- stronger file identity/stability checks;
- fail-closed evidence readback;
- derivation/receipt/record cross-verification;
- managed-path symlink/junction defenses;
- explicit stale-temp cleanup;
- stronger storage durability boundaries.

### Discovery

Canonical main:

`62b679a4809c7496f87d1075b25b3f7a50abf2ab`

Discovery already carries the current 71-total / 53-public / 18-private census.
Older 66/67-repository draft branches are historical and must not be treated as
the current census authority.

## Qualified or active, but not yet canonical

### SQL Connectome

Canonical main:

`ac891a4ba20a518c51233482a5d8be77e30e3a78`

PR #38 strengthens PostgreSQL translation qualification provenance by binding:

- capability fidelity;
- expression-semantic fidelity;
- type fidelity;
- source and target IR digests;
- semantic-evidence digest;
- exact PostgreSQL runtime identity;
- exact engine-validation receipt.

Its original exact head was green across CI, conformance, dependency review and
CodeQL. After marking the PR ready, branch protection correctly refused merge
because required checks had to rerun against the newer main.

A clean current-main restack exists on:

`build/postgres-qualification-v2-mainline-20260928`

Latest constructed head:

`b874f9052e2b7531e1b664e6445f13efb23670e2`

Next action: open/qualify that current-main successor and merge only after the
required checks pass.

### Vera

Canonical main:

`a267e7d555e2c15e51a2e578c1e08095091552f0`

PR #206 exact head:

`be3d11a5b4d3a9880c18e03522f0d4e341b71f99`

Lappy focused successor verification passed:

- 19 focused tests;
- diff check PASS.

Hosted status is mixed:

- Temporal enforcement kernel: PASS;
- R6A0 release package: PASS;
- Temporal pilot: FAIL.

PR #206 is also based on the older 67-repository portfolio cut, so it must not
be merged as a current whole-estate absorption without a 71-repository refresh
and resolution/classification of the Temporal pilot failure.

### Vera Control Plane

Canonical main:

`3916aaa7ae2825020c07057968c7a151d92d53de`

PR #141 exact head:

`f20236ebedc773787991aedf4300698e9a94925e`

Current qualification is mixed:

- Control-plane consolidation validation: PASS;
- VCP integrity: FAIL.

Do not merge until the integrity failure is repaired or independently shown to
be an inherited baseline failure with a clean current-main successor.

### Project Lantern

Canonical main:

`737b939d21ad1f6daf7bb6f944b23eaad0b6a7d7`

The installed Project instructions still require Lantern V3 currentness reads
from exact WoWSQL project `bt2-479e4ad9`.

Live WoWSQL access in this session failed before the V3 preflight could begin.
Therefore Lantern currentness is:

`UNKNOWN_WOWSQL_CONNECTOR_INTERNAL_FAILURE_2026_09_28`

No fallback provider was used.

Project Lantern PR #15 and BT2 PR #49 contain source-level provider-transition
assumptions that conflict with the currently installed Lantern V3 authority in
this Project context. They must not be treated as current runtime authority
without an explicit authority transition plus fresh qualification.

## P0 currentness overlay

The old Project Runner P0 overlay from PR #45 is stale because it bakes
2026-09-24 policy conclusions and old repository heads into a currentness
artifact.

A clean rebuild is in progress against:

- Project Runner main
  `b831f4665300769a72af21b30d099568d5fdb280`;
- corpus Git blob
  `4112da68dcf9e008f042c3284653b006d081f1e3`;
- current ten-subject P0 set.

Live P0 main heads observed for the rebuild:

- BT2: `537d97414711503098ac703cf26ca84d14c650c4`
- Discovery: `62b679a4809c7496f87d1075b25b3f7a50abf2ab`
- Project Lantern: `737b939d21ad1f6daf7bb6f944b23eaad0b6a7d7`
- Project Runner: `b831f4665300769a72af21b30d099568d5fdb280`
- Vera: `a267e7d555e2c15e51a2e578c1e08095091552f0`
- Vera Control Plane: `3916aaa7ae2825020c07057968c7a151d92d53de`
- VeraMesh: `98b74ff77981a5478e20a748bbb94565ad9140c8`
- Vera model training: `3ea9b345988201d78ad9c073254d9d3df15c9ed9`
- Vera Mono: `f24738d181d99fc1c0cc1e8b7d8282f887be5081`
- WorkBridgeMCP: `8707a2e1eaf7de5ce2316567b5e6f1e805c0537b`

The replacement overlay will represent canonical-main currentness directly and
will classify candidates as current-base, stacked, stale, or blocked instead
of forcing current repositories into a PR-centric state.

## Multi-lane execution model currently in use

The work is being attacked concurrently through distinct BT2 lanes:

- One: integration and canonical-main sequencing;
- Rezon: assurance / claim ceilings / semantic composition;
- Achilles: security and publication boundaries;
- Discovery: estate membership and currentness;
- Voss: provenance and exact-subject binding;
- Driftguard: replay/currentness/regression failure modes;
- Hephaestus: build/runtime qualification;
- Masamune: failure localization/debugging;
- Lappy Desktop Commander: independent Windows/local execution and cross-platform verification.

These are concurrent role-separated work lanes over shared durable evidence, not
claims of separate hidden minds.

## Immediate next frontier

1. finish and qualify the rebuilt Project Runner P0 currentness overlay;
2. open and qualify SQL Connectome's current-main V2 provenance restack;
3. refresh Vera's portfolio successor from 67 to 71 repositories and resolve
   the Temporal pilot failure;
4. repair or classify VCP #141's integrity failure;
5. continue current-main restacks through VeraMesh, WorkBridgeMCP, Vera Mono and
   model-training in dependency-safe order;
6. keep Lantern runtime currentness UNKNOWN until exact WoWSQL V3 readback is
   available or the Project authority explicitly changes.

No force-push, provider retirement, credential mutation, destructive cleanup,
deployment, or fabricated qualification is part of this checkpoint.
