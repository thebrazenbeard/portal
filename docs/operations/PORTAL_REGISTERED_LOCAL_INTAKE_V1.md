# Registered local checkout candidate inspection — source V1

This source-only helper, `portal.registered_checkout_intake.inspect_registered_checkouts`,
examines an existing `PORTAL_EXISTING_CHECKOUT_INDEX_V1` file without cloning,
fetching, pushing, editing source checkouts, or reading GitHub credentials.

It returns deterministic `RegisteredCheckout` records containing repository,
subject ID, checkout path, current HEAD, source ref and `already_observed`.
The caller supplies the subject IDs already present in its durable session; an
already observed or held subject is **not** a new candidate merely because its
source head changed.

Every registered checkout must be an absolute non-symlink directory with a
matching GitHub `origin`, a 40-digit commit HEAD, a named local source ref,
and clean source/index/untracked state. A malformed record invalidates the
whole scan rather than silently shrinking the inventory. The owner and
subject-ID uniqueness are checked case-insensitively.

This scanner **does not establish authenticated GitHub ownership, private/public
visibility, repository currency against a remote, independent trust, execution
authority or a complete portfolio census**. A writable index and forged Git
origin are not an attested admission. It is not safe to promote its records
directly into an effect-bearing wave without independent membership/currentness
checks and the existing wave/corpus/registry validation.

The running PR22 desktop task is *not* changed by this branch. Its registered
index currently contains only Firesafe, which is `ALREADY_OBSERVED`. New
registered clean checkouts can be surfaced as local candidate inputs; later
work must perform governed candidate -> wave admission and verify unattended
resumption separately, without replaying held proposals.

Adversarial review: scanning local checkout paths is not in itself autonomous
work intake. **Accepted.** This is a narrow, testable trust boundary ahead
of an actual host-fed queue, not evidence that the production queue refills.
