# Host-owned pre-route STOP_PLAY (opt-in source seam)

The `ResidentCognitionEngine` can enforce an explicit `STOP_PLAY` from a
trusted, in-process host decision **before** route discovery, model generation,
acceptance, or conversation-memory update. A denied request is persisted as
`FAILED`, `retryable=false`, `error=host_pre_route_STOP_PLAY`. An exception,
missing decision, malformed action, or a decision bound to a different
request fails closed with `host_pre_route_admission_failed` and is likewise
nonretryable. Replaying either request does not rerun the gate or inference.

The caller opts in through the engine constructor's `pre_route_gate` callback.
It receives the full request envelope and a fresh `cognition-attempt:`
SHA-256 binding derived from the `PORTAL_DESKTOP_COGNITION_EVIDENCE_V2`
request claim and the claim's unpredictable attempt token. It must return an
exact `PreRouteDecision` bearing that `attempt_binding_id` and action `ALLOW`
or `STOP_PLAY`. This binds the decision to source, reason, task, creation
timestamp, required capabilities, opaque conversation-session binding **and
the current attempt**. The raw attempt token stays inside the engine.
Decisions cannot be replayed for a different task, session, or retry after
a transient failure.

The model and ledger use a private request snapshot. The callback receives a
**different** envelope object; a change to that clone is detected and
rejected. Even a caller mutating the original frozen dataclass via
`object.__setattr__` cannot retarget the task executed by a model while the
ledger still records its original text. This guards the demonstrated
TOCTOU counterexample, not arbitrary mutation by already-trusted host code.

This is **not** an authorization/token mechanism for arbitrary callers:

- The desktop file-bridge payload does not accept `STOP_PLAY`, an admission
  override, or any new policy fields. Merely typing `STOP_PLAY` in a prompt
  cannot trigger an authority-bearing policy decision.
- The host is responsible for configuring and trusting the callback; a
  malicious or incompetent callback can lie. No cryptographic attestation of
  the policy producer or external evaluator is claimed.
- With the default `pre_route_gate=None`, existing installed/runtime behavior
  is unchanged. This branch alone does not install, wire, or qualify a donor
  to produce this decision. No behavioral or weight-space transfer is proven.
- The engine's effect authority ceiling remains cognition-only; this gate
  does not grant protected effects, memory admission, or deployment authority.

**Hostile invariant:** A post-generation `ACCEPTED_HOST_OBSERVATION` receipt
does not prove the STOP_PLAY boundary was respected. Qualification requires
an effect-traced test in which a host-attested same-request veto occurs and
route discovery, model invocation, and result acceptance callbacks are **not
called**. Caller-supplied, unbound, copied, or invalid decisions must not
silently turn into `ALLOW`.

This is a source-level enforcement primitive awaiting an exact host-side
admission owner and external behavioral qualification. It is not an active
Vera runtime, and no `main` merge or Windows task installation is implied.
