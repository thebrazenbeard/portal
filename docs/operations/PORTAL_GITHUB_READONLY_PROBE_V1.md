# GitHub exact-head observation through PORTAL's existing host probe V1

**Authority:** observation only. No tool effects, no inferred command or write
authority, and no current Vera runtime qualification.

The host may use the existing `PORTAL_HOST_COMMAND_PROBES_V1` manifest
to invoke `python -m portal.github_head_probe` as a read-only `gh api`
source-head observer. The operator provides an exact expected commit SHA.
Use a fresh commit/head readback before publishing the probe manifest.

Example probe manifest, replacing `<exact-40-char-sha>`:

```yaml
schema: PORTAL_HOST_COMMAND_PROBES_V1
probes:
  - adapter_id: github-head-observer
    command:
      - python
      - -m
      - portal.github_head_probe
      - --repository
      - thebrazenbeard/portal
      - --ref
      - main
      - --expected-head
      - <exact-40-char-sha>
```

The observer invokes `gh api --method GET` with `shell=False`; it neither
executes a repository command nor changes GitHub resources. Missing CLI,
missing/revoked repository access, invalid JSON, missing commit SHA, or a
failed HTTP response produces a failed probe, NOT an authoritative route.
An observed commit differing from the expected head emits `current=false`.
The route's `effect_capabilities=[]` deliberately prevents dispatch even
when `available=true`, `attached=true`, and `current=true`.

`evidence_id=github-head:<repository>:<ref>:<expected-sha>:<observed-sha>`
is an **untrusted, request-scoped observation ID**. Distinct repositories,
refs, or expected heads must not reuse one evidence identifier merely because
the GitHub API returns the same observed SHA. Unsupported Git ref syntax is
rejected before CLI invocation. This is NOT an authenticated GitHub attestation,
a local checkout verification, or a claim that the current process consumed
the repository. PORTAL must still bind current TTL and any effect permission
through its existing independent host authority and route/attempt gates.
Do not use this source observer to authorize a GitHub write: a different,
explicitly qualified execution driver and grant are required.

The tests validate exact match, mismatch, invalid payload/locator, probe
failure and interoperation with the existing host-probe parser; one live
read-only GitHub API probe also verifies real CLI integration. No external
effects are performed in this acceptance.
