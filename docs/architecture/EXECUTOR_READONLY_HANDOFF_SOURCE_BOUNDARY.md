# Source-only Executor read-only envelope — qualification boundary

This is **not** an Executor adapter, authenticated host session, execution grant, installed service, or a replacement for Portal route admission.

`portal.executor_readonly_envelope.verify_and_reserve_readonly_envelope` consumes only a small JSON metadata request. It permits one literal operation, `READ_GITHUB_REFERENCE_METADATA`, under `NO_EFFECT`, with a caller-pinned issuer, repository, ref, immutable Git commit, target, dispatch ID and attempt ID. A caller-supplied HMAC key checks bytes, not host identity. SQLite records dispatch, attempt and nonce uniqueness before the function returns. Successful output explicitly has `execution_authorized=False` and `host_authenticated=False`. It **does not issue or run a command**.

## Unresolved external ownership and security obligations

The live owner must independently bind issuer identity to a protected, rotated key. A shared HMAC secret is not proof of separate origin, consent or remote identity; leaked keys permit forgeries. No production key exists in this source.

The caller must independently verify the current Git ref equals `exact_head`, and pin the actual Lappy/Executor service identity by a host-owned trusted channel. Presence of devices, a server-name claim, a TLS-verified GitHub fetch, or an echoed child result supplies no execution authority.

The nonce database and its parent must be under a host-owned, access-controlled, durable root protected from replacement, rollback, backup restoration, deletion, untrusted symlinks and disk faults. The verifier checks for unexpected schema and triggers and serializes normal concurrent reservations, but cannot defeat an attacker with filesystem control or certify cross-host replay suppression. No canonical writer or storage custody is provisioned by this code.

Production calls should use the built-in UTC clock; `now` and `clock` are explicit test overrides and are not independent trusted-time evidence. The expiry check is repeated after acquiring the SQLite write lock, but real-time preemption or clock skew still require system qualification.

Do not register a host driver, add route authority, claim an effect, or merge this stacked draft on the strength of these tests. No effectful Portal→Project Runner→Executor path has been exercised.

## Evidence and recovery testing

Source tests exercise signature rejection, strict field and identifier grammar, exact-scope pinning, expiry, duplicate keys, no-effect semantics, concurrent reservations, reused nonces/dispatch IDs, schema-trigger tampering and a simulated lock-delay clock change. They are not independent host review, actual Executor authentication, service install, or production rollback testing.

Portal PR #88 owns `portal/host_command_driver.py` and its known hostile RED cases for full-dispatch child forwarding and duplicate driver result JSON keys; do not reinterpret this new module as fixing that driver. Portal #113 owns package namespace migration; Portal #114 only parses observational Executor discovery, and standalone Project Runner #96 proves a separate read-only GitHub reference-worker path.

A later, independently reviewed authorized handoff requires an owner-attested Executor endpoint, explicit exact-operation grant, attested immutable target, fenced attempt and receipt identity, multi-host replay persistence and crash/retry reconciliation, all under Patrick's exact effect authority.
