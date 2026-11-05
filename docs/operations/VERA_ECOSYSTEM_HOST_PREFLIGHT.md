# Unified host preflight (source-only, observational)

This read-only gate checks whether *reported* process and HTTP observations
justify proceeding to a separate installed-runtime qualification. It neither
starts services nor produces authorization to activate a runtime, dispatch
work, trade, deploy, write memory, or call a protected tool.

Run from an installed or source-bound Portal checkout:

```sh
python -m portal.unified_host_preflight --evidence fresh-private-host-observations.json
```

Exit status 0 means `OBSERVATIONAL_PREFLIGHT_PASS`, after which the actual
installer / route / runtime-consumption / effect qualification still must run.
Exit 2 means `HOLD`; exit 3 means invalid evidence. The CLI consumes one
fresh, operator-produced non-secret JSON receipt with exact fields; it does
not take provider credentials, tokens or IP addresses. Observations expire
after 120 seconds by default (maximum configurable 3600 seconds).

The required gates use abstract host roles rather than personal machine names:

| Role | Probe ID | Required observation |
| --- | --- | --- |
| control | `control.vera_mono_pilot` | Windows service `RUNNING` |
| control | `control.veramesh_tunnel` | Windows service `RUNNING` |
| control | `control.ollama_tags` | HTTP 200, no credential |
| worker-a | `worker_a.ollama_tags` | HTTP 200, no credential |
| worker-b | `worker_b.rezon_healthz` | HTTP 200, no credential |
| worker-b | `worker_b.rezon_readyz` | HTTP 200 **with credentials actually applied by host observer** |
| worker-b | `worker_b.llama_models` | HTTP 200, no credential |

`model_selection` additionally requires a nonempty selected model tag, raw
64-character lowercase SHA-256 digest, and an observed zero-incremental-paid
compute route. A 200 `/api/tags` response alone is insufficient. A 401 from
Rezon `/readyz` with no credential is *not* evidence that Rezon is down; it is
also *not* an authenticated readiness PASS.

`tests/fixtures/unified_host_reported_hold.json` is a non-secret reconstruction
of the readbacks relayed to this source-review session on Nov 5, 2026, not an
original, independently attested host receipt. Its timestamp records assembly
of the reconstruction, **not** the execution time of the underlying probes.
The control services were reported STOPPED; Lappy and VN1 Ollama `/api/tags`
were 200; VN2 Rezon `/healthz` was 200 and unauthenticated `/readyz` was 401;
VN2 llama `/v1/models` was 200; exact model selection and route cost evidence
were not supplied. The verdict must be `HOLD`.

## Hostile-review limits

> A caller can forge a JSON receipt, mark `credential_applied=true`, or copy
> old observations into a freshly timestamped envelope. A preflight PASS is
> **not** authentication, attestation, host integrity, or runtime qualification.
> Real startup release still requires host-owned observation provenance,
> exact installed source attestation, route currentness, and effect fencing.

This gate intentionally does not touch the `portal`/`project-runner` packaging
conflict. Portal main still owns an overlapping `runner` wheel; isolated
distribution ownership and integration are under separate unmerged draft PRs.
