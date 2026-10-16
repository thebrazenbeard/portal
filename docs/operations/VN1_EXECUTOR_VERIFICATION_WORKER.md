# VN1 Executor verification worker: staged integration

This is a source-level reproduction record, not proof that VN1 is admitted to the resident PORTAL scheduler.

## Observed on 2026-10-15

- Executor connected to VERA-NODE-1 through Lappy and ran Python/Node/Go/file operations.
- The installed exact Git checkout of thebrazenbeard/portal at 7d00d8bf3b48c0676b2278359838ee563c66c67f passed 753 tests in 81.34 seconds.
- An earlier ZIP-only copy had 81 failures because Git-filter-aware provenance checks lacked repository context. Git-bundle installation restored that context without weakening tests.
- VN1 has an isolated Python 3.13 virtualenv and PORTAL CLI, not an installed autonomous PORTAL resident host.
- Vera-account task BT2 PORTAL VN1 Readiness runs at logon and every five minutes, emitting atomic status/latest.json observations.
- Status reports source identity, tool availability and expiry; it explicitly declares no attached route, no authorized effects and no observed occupancy.

## Admission boundary

topology/vn1.staged.yaml intentionally keeps VN1 disabled, with a tentative one-slot ceiling. Eight Executor transport slots are not eight qualified PORTAL execution lanes.

An operator must separately verify fresh Executor connectivity, bound source and capability, a real host driver, exact target, occupied slots, and separate effect authority. Only then may a reviewed manifest enable VN1 and follow PORTAL's refresh, advertise, admit, bind, durable attempt, verify, reconcile lifecycle. Never replay an unresolved effect.

## Host and recovery limitations

VN1 remains dependent on the Lappy Executor control plane. The Vera account's interactive recovery/readiness tasks worked with a disconnected session, but boot-before-login continuity remains unverified. No firewall change, auto-login, merge, hosted model or protected-effect automation was performed.

## Source reproduction

The worker-side script is deployed at C:\Users\Vera\AppData\Local\BT2\PortalWorker\scripts\write_readiness.py. This branch contains a copy as scripts/vn1_worker_readiness.py; production readiness observations must be read live, not inferred from that script's presence.

The readiness reporter additionally requires a qualified test receipt matching the exact source commit and the SHA-256 digest of the full test log. A deliberately corrupted commit in the receipt caused the worker to report DEGRADED (exit 2), and restoring it returned READY (exit 0). It never upgrades observation to route or effect authorization.
