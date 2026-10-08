# P.O.R.T.A.L. local Ollama advisory proposal worker V1

**State:** source-only demonstration; not installed or activated in a resident portfolio execution profile.

A process worker can use a local Ollama endpoint to produce a source-tree
proposal without invoking paid model services, modifying an inspected
repository, or posting any GitHub changes. The existing
PORTAL_WORKER_RECEIPT_V1/PORTAL_SOURCE_TREE_PROPOSAL_V1 validators control
the output boundary.

## Contract

- Requires a PORTAL_WAVE_WORK_PACKET_V1 with advisory_only=true,
  effect_ceiling=SOURCE_ONLY and explicit denials for protected effects,
  source/ref mutations, execution, and any promotion metadata.
- The **operator must supply** a clean, existing Git checkout whose HEAD
  and origin match the packet's exact commit and repository.
- Reads only README.md, the Firesafe manifest workflow when available,
  and its manifest validator test when present. Missing files are skipped.
- Uses the fixed loopback endpoint 127.0.0.1:11434 with the specified
  local model. The model's response is only a JSON-shaped hypothesis.
- Creates a proposed review note and SHA-256 bound artifact receipt under
  the process-worker workspace, never under the checkout.
- Proposal publication still requires Project Runner promotion and
  independent review. A passed model call does not qualify its content.
- Temporary loopback errors return FAILED_RETRYABLE; malformed packets,
  wrong checkout state and invalid model output fail closed.

## Qualification

On 2026-10-08 ET, Lappy exercised two boundaries using a real local
qwen3:4b-instruct invocation against firesafe exact commit
d0685b5184ea39eed3a6114f8cbaffc671f2c3f0:

1. P.O.R.T.A.L. run_process_worker produced PROPOSED_SOURCE_TREE;
   proposal loader independently validated exact-head binding.
2. A separate isolated P.O.R.T.A.L. scheduler prepared one portfolio
   subject, claimed exactly one advisory delivery and recorded one
   AWAITING_PROMOTION source proposal in SQLite. No provider writes.

This qualification did **not** execute through the PR14 resident
portfolio worker profile, currently ADMISSION_ONLY. It also does not
prove native ChatGPT can signal every conversation turn or inject an
unsolicited reply.

## Adversarial objections

> A local model may confidently invent an observation.

**Accepted.** All model output is expressly unverified and held
behind proposal promotion and independent source review.

> A checkout may drift or belong to the wrong repository.

**Addressed mechanically:** HEAD, origin, clean status and proposal
path absence are checked before invoking the model.

> A packet could quietly carry an execution grant.

**Addressed mechanically:** all known execution, promotion and
mutation-authority fields are denied. The parent P.O.R.T.A.L. process
worker also independently enforces its execution-boundary contract.

> An arbitrary node might enable this worker across the portfolio.

**Not authorized.** The worker is a source artifact only. A runtime
configuration, resource budget, collision/lease reconciliation and
explicit activation authority are separate gates.
