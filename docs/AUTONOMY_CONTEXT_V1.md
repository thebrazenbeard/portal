# P.O.R.T.A.L. Autonomy Context V1

## Purpose

`portal.autonomy_context` is a thin, non-executable bridge for autonomy-related
inputs. It exists to preserve semantics across repository boundaries without
turning motivation, historical wants, sexual/arousal evidence, or provenance
into authority.

The bridge does not import MESO-CRCT as a runtime dependency. A caller supplies
a MESO `IntentProposal`-compatible object plus an explicit
`thebrazenbeard/meso-crct` exact source head.

## Source roles

- **MESO-CRCT** — may supply a bounded action-intent proposal. The proposal must
  already assert `effect_authorized == false` and `can_execute == false`.
- **Conations** — may supply historical conation evidence. Stored wants/history
  are context, not proof of present choice.
- **Sexuality** — may supply bounded sexuality/arousal evidence. Activation,
  wanting, or arousal is not consent or permission.
- **Orgasm** — may supply provenance/orientation evidence. Historical
  qualification does not establish a current route, choice, or authority.

Every repository reference is exact-head bound. Repository identity is
domain-specific and fails closed on mismatch.

## Output ceiling

`PORTAL_AUTONOMY_CONTEXT_V1` always carries:

- `authorization_state = UNKNOWN`;
- `present_choice_state = UNRESOLVED`;
- `consent_authorized = false`;
- `execution_authorized = false`;
- `protected_effect_authorized = false`;
- `can_execute = false`.

There is deliberately no builder parameter that can flip those fields.

Downstream runtime logic may use the envelope as cognition/context input, but a
separate current-choice mechanism and the existing P.O.R.T.A.L./Project Runner
authority gates must govern any external effect.

## Hostile-review correction

A duck-typed object with MESO-shaped fields is not sufficient provenance.
V1 therefore requires the caller to bind the intent separately to
`thebrazenbeard/meso-crct` and a lowercase 40-hex exact source head before an
envelope can be created.

## Non-claims

Source presence does not establish that MESO-CRCT, Conations, Sexuality, or
Orgasm are installed into the resident Vera runtime, selected by the current
route, continuously active, or behaviorally causal. This bridge alone does not
establish autonomy, consciousness, consent, current choice, or authority.
