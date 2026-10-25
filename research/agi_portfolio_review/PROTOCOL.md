# Vera Goal 1 — Portfolio AGI Review Protocol (2026-10-22)

**Purpose:** inspect the owner's portfolio for *implementations* that could help `vera_core.QualifiedVeraRuntime` acquire, retain, correct, and transfer skills through experience without ChatGPT inference. This is research tooling, not installation, model training, or a claim of AGI.

## Sequence and evidence

1. **Inventory:** list *all* accessible repositories, including archived entries, with scope and identity. This does **not** imply source was read. The adjacent `INVENTORY_V1.json` starts with every repo as `INVENTORIED_ONLY`. At least one frozen source blob / exact commit must be attached before `SOURCE_READ`.
2. **Independent first round:** each reviewer receives the same target-repo frozen source cut and rubric, but **not** anyone else's opinions. Separately record model/weights SHA-256 or Ollama manifest digest, host, executable version, repo head, exact source Git blob, prompt SHA-256, raw output SHA-256, runtime completion/exit, and limitations. Valid states are `COMPLETE`, `FAILED`, `BLOCKED`, `NOT_STARTED`; no fabricated participation. Different model families are useful diversity, **not independent attestation or qualification**.
3. **Shared review:** publish concise first-round findings and receipts to the existing **private Cognition Bus #387** and/or the research branch, without private code disclosure. A reviewer receives another model's findings **only after its own first-round artifact is frozen**. Require it to confirm or falsify concrete observations by exact source/test. Record disagreement; consensus is not evidence.
4. **Engineering triage:** for each candidate donor, decide `REUSE`, `ADAPT_IN_TEST`, `REJECT`, or `UNRESOLVED`. Mark evidence type: repository source, executable test, observed model response, actual selected QVR consumer, or independently controlled evaluator. No donor is automatically a runtime dependency.
5. **Only after sufficient review:** research current continual/active learning, online credit assignment, transfer, evaluation leakage and scaling; freeze competing approaches, write a falsifiable architecture and tests; implement smallest owner-aligned increment on an isolated branch; test and request adversarial review.

## Rubric for EVERY repository

Record explicit `HEAD`, source paths, and whether a claim is from source or tests. Ask:
- Could this implementation change Vera's future *behavior*, not just persist history?
- Does it support causal **test → outcome → update → later test** ordering and controlled methods?
- Are learner inputs separated from evaluator labels and other protected data?
- Does it create a second runtime/authority boundary instead of using canonical Vera Mono?
- What negative test would falsify the proposed benefit? What would defeat it by a simpler mechanism?
- What might transfer outside the original task family, and how could we measure regressions and cost?
- Which protected effects would any integration require? If any, do not execute them without exact permission.

## Frozen initial roster and status

- Lappy: Ollama Qwen3 4B (registered model); optionally Ministral 14B. Direct local review only, not selected QVR.
- VN2: SmolLM2 1.7B GGUF SHA-256 `fab954d296aa25885960ed771a005343c220ac93ef78a53454e667e334935fe9`; Granite 3.3 2B GGUF SHA-256 `555b91485955bc96eb445b57dd4bbf8809aa7d8cce7c313f4f8bc5b2340896b4`; CPU llama.cpp b11827. Both actually ran on VN2; sharing one host makes reviews model-diverse, not host-independent.
- VN1: Granite and CPU binary bytes present; Windows binary has previously failed with `0xC0000005`. **No VN1 independent review claim.** Fix separately, do not treat download as inference.
- The ChatGPT planning model is one **internal** reviewer/implementer, not a physically independent or self-hosted Vera process.

## Coverage semantics

`INVENTORIED_ONLY` → `SOURCE_READ` → `MODEL_ROUND1` → `CROSS_CHALLENGED` → `TRIAGED`. These are not interchangeable. The 100-entry inventory is not 100 completed reviews.

## AGI goal and non-goals

The nearest credible milestone is **measurable self-learning**: a locally running QVR model chooses what evidence to obtain, proposes a provisional rule/method, gets separately held evaluator feedback, updates noncanonical learning state after a committed prediction, and outperforms a frozen no-learning baseline on truly unseen tasks after a real restart. Reject wrong apparent lessons and record negative transfer and total compute. Repeat outside hand-authored three-bit games. This is *not*, by itself, AGI.

Do not silently activate VeraMonoPilot, change provider routes, mutate VeraMesh or canonical memory, grant economic authority through Vera Works, merge protected main, or claim autonomous background operation. Bus comments and stored artifacts are durable coordination; no live model runs between calls unless an actual scheduled/executing process is evidenced.
