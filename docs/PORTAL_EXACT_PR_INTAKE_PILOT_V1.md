# Exact-head open-PR intake — source-only pilot

Status: DRAFT / NO RUNTIME ADMISSION. Base: Portal main `a27db806c064248cba5cf109aa8a4866df4fb8d1`.

Portal already owns repository discovery, durable scheduling, Project Runner claims,
collision-safe waves, continuous refill, and live execution routes. This pilot adds
the missing **per-open-PR observation boundary**, not another scheduler.

## Local invocation

Requires Python 3.12+ and the existing Portal dependencies.

```bash
# Public-only proof; writes an explicitly chosen local file once.
python -m portal.pr_intake --owner thebrazenbeard \
  --output /an/operator/controlled/path/pr-snapshot.json

# Include authenticated private PRs only in a local access-controlled destination.
PORTAL_GITHUB_TOKEN="<provided to the process, never printed>" \
python -m portal.pr_intake --owner thebrazenbeard \
  --expected-repos 118 --include-private \
  --output /an/operator/private/path/pr-snapshot.json
```

The expected repository count is a **live guard**, not a hardcoded portfolio
truth: remove/change it only after verifying the observed authoritative count.
The command performs GitHub REST GETs only. It does not schedule native agents,
write GitHub, approve PRs, merge, close, deploy, train, or spend funds.

Private PR records are never committed as an output artifact. The script rejects
any output path inside Portal's source tree, creates only nonexisting files, and
prints only aggregate counts. For private scope, use an approved secured local
storage path and host credentials already authorized for this task.

## Candidate record

Every open PR discovered in the included scope produces one
`PORTAL_PR_INTAKE_CANDIDATE_V1` record with:

- Stable source identity `github-pr:owner/repo#number`.
- Immutable observation identity incorporating exact head SHA, changed-file digest,
  and the PR's exact base SHA captured at intake.
- Full paginated changed-file metadata (filenames only; includes rename origin).
- A read-only verification GET after changed files are read; head/base movement
  during the scan rejects the entire run.
- File-scoped keys for reviewer collision hints and repository-scoped keys for
  future mutation arbitration. No worker claim is made.
- Default `UNREVIEWED` disposition; `KEEP`, `CONSOLIDATE`,
  and `SUPERSEDE` are suggestions requiring independent evidence and
  authorized human review, not automated classifications.

Malformed payloads, duplicate PR IDs, changed heads, API failures, invalid paths,
and pagination exceeding the cap abort instead of writing a partial snapshot.

## Next admission gate — not implemented in this pilot

A future governed adapter can *revalidate* each exact PR head against live
GitHub, compare branch/repo mutation ownership, then bind the candidate to
Project Runner's existing fenced work unit and Portal's wave scheduler. It must
also define reviewer rights, bounded active-agent budget, durable receipts,
private coordination access, stale-head invalidation, and disposition provenance.
One logical candidate per PR does not imply an unlimited concurrent ChatGPT
subagent or automatic code mutation. Workers released after completion can
be refilled from the durable queue.

CCB/Intranel material may be attached as typed evidence only. Rezon may supply
reasoning/review only. None grants ownership, consent, or remote effects.

> Hostile challenge: One PR can touch many repos indirectly; identical filenames
> are not the only semantic collisions. A changed-file overlap is evidence for
> review arbitration, never enough to authorize a source write. Recheck the full
> dependency and semantic subject graph before admitting any write-capable lane.

## Evidence classification

- SOURCE: the new read-only adapter and tests exist on a draft branch.
- CI: evaluate the exact PR-head GitHub Actions runs independently.
- LIVE HOST: no installed/resident runtime, background loop, worker admission,
  or deployment is claimed by this source-only PR.
