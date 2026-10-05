# Project Runner Portfolio Corpus

The Portfolio Corpus is Project Runner's descriptive inventory of the user's project estate. It is not the execution registry and it is not authorization.

## Why this is separate from registry/projects.yaml

`registry/projects.yaml` answers which projects Project Runner may schedule under explicit assignment/review/capability constraints.

The corpus answers a different question: what projects and durable workstreams exist, what family they belong to, how active they are, and what current frontier was observed.

Priority therefore does **not** grant scheduling or effect authority.

## Public/private split

This repository is public.

`corpus.public.json` enumerates every public repository in the audited estate and every public workstream in the audited workstream set.

Private repository and private workstream names are intentionally absent. The public corpus preserves:

- total/public/private counts;
- archive counts;
- private repository/workstream aggregate counts only in the public projection;
- no deterministic public digest of private names or private workstream IDs. Exact private membership belongs to an external private corpus or a keyed commitment mechanism.

A complete private corpus may be stored outside this checkout and validated with `load_portfolio_corpus(..., complete=True)`.

## Audit cut

The V1 cut observed on 2026-09-24 contains:

- 66 repositories total;
- 48 public repositories;
- 18 private repositories;
- 2 archived repositories, both private;
- 15 known non-repository workstreams;
- 2 public workstreams;
- 13 private workstreams.

The observed status text is not a freshness oracle. Live repository/runtime evidence must be refreshed before operational scheduling, merge, deployment, or claims of current completion.

## Priority meaning

- **P0** — portfolio/runtime spine or current dependency bottleneck.
- **P1** — high-value active front with substantial scientific, product, research, or architectural leverage.
- **P2** — useful subsystem, specialist, assurance mechanism, or incubator.
- **P3** — domain/creative/history project worth preserving but not currently portfolio-unblocking.
- **P4** — archived, superseded, or deliberately non-current.

These are portfolio triage categories, not permissions.

## Validation

The schema is `schemas/portfolio-corpus.schema.json` and the loader is `runner/portfolio_corpus.py`.

The loader rejects duplicate repository identities, inconsistent aggregate counts, private identifiers in the public projection, incomplete public projections, and incomplete/mismatched full private corpora.
