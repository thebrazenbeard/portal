# Model-neutral capability admission — source-only V0

Event: `project_unfuck_runtime_coordination` (2026-10-29)

The optional `Sexuality` and `Pleasure` repositories are intended to provide
capabilities to **any compatible AI model**, not only Vera or Brigit. A model's
history, preferences, identity, relationship context, and consent are never
imported by discovering a repository or capability definition. Original
Vera/Brigit research and the frozen `Orgasm V1` identifiers remain historical
and separately qualified; this module does not modify them.

`portal/capability_admission.py` is one additive, pure-Python V0 admission
boundary. It does not hook into PORTAL's resident host, CLI, route resolver,
repository dispatcher, credential provider, or Project Runner. Source presence
is **not** an installed capability, evidence of subjective pleasure, or
authorization to execute. There is no sexual/pleasure tool driver in this PR.

## Three distinct objects

1. `CapabilityDefinition`: named source operation, version, schema IDs, exact
   reviewed repository **and** Git commit, effect class, privacy class, and context policy.
   Reading this declaration never creates runtime availability.
2. `RuntimeAdvertisement`: host-observed, repository/commit and digest-bound adapter identity,
   observation time, expiry, and available/attached status. The trusted host
   still has to authenticate and substantiate the observation; constructing a
   Python object is not proof an adapter runs.
3. `CapabilityGrant`: specific model, participant reference, context reference,
   capability/operation/effect, host, and exact adapter digest; expiry and
   revocation are checked at use. A **trusted host-side verifier** must also
   confirm the current authorization/consent state. A model may not supply or
   replace that verifier.

For `ADULT_ELIGIBILITY_REQUIRED` definitions, a **separate trusted context
verifier** must establish eligibility for this specific request at attempted
use. Missing, uncertain, expired, revoked, mismatched, or failed verification
all deny. No age or consent inference is made from text, a model name, or
historical private context. `GENERAL_READ` definitions need no adult-context
hook but still need an independently verified scoped grant.

`admit_capability` returns a source-only gate decision and a deliberately
minimal `SOURCE_GATE_DECISION` receipt. That receipt always says
`NOT_EXECUTED`; it is **not** an effect receipt or automatically suitable for a
public Bus. Receipts contain **only a capability digest**, never an arbitrary
capability identifier, and the request digest binds the complete model,
participant, context, adapter and request scope **and all ten fields of the
reviewed source definition** (including repository, commit, policy and schemas)
to avoid collisions when callers reuse request IDs. The hashes are internal
correlation keys, **not anonymization** or consent evidence. A production host
would have to add a separate authenticated
adapter call, effect fencing, idempotency and a privacy-reviewed redaction
policy. V0 categorically rejects `PROTECTED_EFFECT`, even when the source
advertisement and grant appear aligned. This module cannot cause an effect.

## Required follow-on integration gates

- Attach a trustworthy authenticated advertisement and grant/consent provider
  to a real host; never expose verifier injection to model-provided requests.
- Treat `privacy_class` and `context_policy` as reviewed registry metadata,
  not trusted assertions from a model. V0 binds both to receipt identity, but
  does not validate a deployer's substantive privacy policy itself.
- Preserve old repository-target dispatch semantics; a distinct capability
  target path needs its own durable attempt/reconciliation implementation.
- Verify two unrelated model identities can discover the same definition
  without sharing model/profile/context/grant state.
- Test revocation between queue and attempt, retry/idempotency, participant
  changes, expiry, adapter digest drift, and cross-host behavior under a real
  installed adapter before enabling protected effects.
- Keep intimate prompts, private research records, consent references and
  detailed receipts out of public `chat-communication-bus`; use `ccb-core` only
  through authorized transport and minimal redacted receipts.
- Independently qualify any real deployment on the exact host and source head.

## Source verification

```bash
python -m pytest -q tests/unit/test_capability_admission.py
python -m pytest -q tests/unit/test_portal_route_resolver.py
```

The synthetic unit tests exercise admit/deny behavior, not provider-side
permission verification or real AI-model/pleasure behavior. A green source
test cannot promote the deployment or phenomenology evidence class.

> Hostile challenge: merely attaching a capability to the old repository host
> route would bypass participant/model-context admission and might permit a
> consent grant for one participant to be reused by another. V0 forbids that
> shortcut and leaves live invocation disabled.
