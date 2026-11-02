# Source-only metadata MAC and local replay boundary

**Status: DRAFT / NO REMOTE EXECUTOR EFFECT / LOCAL SQLITE WRITE / NOT INSTALLED / NOT AN EXECUTOR ADAPTER**

`portal.executor_readonly_envelope.verify_and_reserve_readonly_envelope` parses a small JSON metadata envelope for one literal operation, `READ_GITHUB_REFERENCE_METADATA`, with the label `NO_EFFECT`. These strings do not enforce capability limitations on a separate caller. This module performs **no GitHub fetch, Executor command, transport, dispatch or remote effect**. Successful verification DOES create or write local SQLite replay state, including possible journal files and locks.

The caller must receive a `ReadonlyEnvelopeTrust` instance constructed by an independently trusted owner. The context rejects shared issuer key material and snapshots the configured `issuer_id -> HMAC-SHA256 key` registry and a single absolute SQLite reservation path. The verification request cannot override a key, database path, or clock. A caller able to construct or replace its own trusted context can still impersonate issuers and select fresh replay history; **this source does not establish the owner or authenticate a remote identity**.

The MAC has an envelope-specific domain prefix. Exact operation, issuer, repository, ref, Git SHA, target, dispatch and attempt are checked against an independently supplied expected scope. An authenticated MAC means only that the signer possessed the configured secret; it is not proof of the signer's unique identity, Lappy's identity, an Executor endpoint, live Git HEAD or effect permission. Results explicitly expose `execution_authorized=False` and `host_authenticated=False`.

SQLite uses an immediate transaction and unique keys within an issuer for nonce, dispatch and attempt. The function checks the existing table declaration, the expected index shapes, and rejects persistent triggers on the reservation table. The callback-injected clock exists only in a private testing helper; the public API reads local wall time, checking before and during the write transaction and immediately before commit. Failure after a successful durable commit but before a response can strand a request; there is no reconciliation API here. Storage errors are distinguishable from replay rejections.

**Replay custody remains an external high-priority requirement.** A host-owned process must protect its reservation database, complete parent path, journaling files, backup/restore history, filesystem ACLs and rollback/replacement boundary. Current path and schema checks do not prevent symlink races, hardlink/reparse-point attacks, or malicious replacement of the database. Separate databases on different hosts do not share replay state. Clock changes and scheduling after the final expiry check remain outside this code's guarantees. There are no production keys or persistence authorities bundled with this branch.

Stored metadata includes issuer, nonce, dispatch ID, attempt ID and payload digest. These can identify or correlate operational work; the production owner must define access, retention, redaction, backup and deletion policies. No private conversation data belongs in this envelope.

## Required downstream qualification

Before connecting the module to real host work: independently bind issuer and target to authenticated provider identities; establish source currentness and exact effect authority; qualify a protected shared replay/receipt service and crash recovery; ensure that the actual adapter is mechanically restricted to the approved read-only operation; and test an end-to-end authenticated route through Portal -> standalone Project Runner -> dedicated Executor using real custody evidence.

This draft is **stacked on Portal PR #113** (package ownership). PR #114 is observation-only; Project Runner PR #96 proves a separate GitHub source reconstruction route. Portal PR #88 owns its command-driver boundary, where private-field forwarding and duplicate driver JSON keys remain reproduced and unresolved. Passing this module's synthetic tests or hosted source CI does not upgrade those facts.

No merge, install, deployment, credential mutation, global trust change, spending or production action is authorized or implied by this source.
