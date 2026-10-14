# PORTAL: bounded 89-project read-only qualification

## Scope and execution boundary

This interface qualifies a fresh, exact-count portfolio against authenticated
GitHub metadata. It does not run repository code or claim that a project was
advanced, repaired, merged or deployed. Its receipts are explicit:
work_completed = READ_ONLY_QUALIFICATION_NOT_PROJECT_EXECUTION.

Use a user-authorized, isolated Windows checkout with the GitHub CLI already
authenticated. This command reads public/private metadata into a local
operator-controlled directory; it must not be committed to a public repo:

    python -B -m portal.portfolio_readonly_batch --owner thebrazenbeard --count 89 --max-parallel 8 --output D:\VERA\scratch\portal-qualification-new-unique-name

The source rejects an unexpected count, duplicate repository, missing archive
or visibility field, mismatched owner, invalid head, and max concurrency > 8.
Archived entries and the exact owner account-profile repository are excluded.
No silent truncation is allowed if the owner's inventory changes.

Each worker performs authenticated read-only GitHub repository, default
branch, root tree, PR and recent-main-CI reads, then writes an ATTEMPTED
receipt before the work and an atomic COMPLETE or HELD receipt at the end.
Errors remain HELD and do not imply automatic retry. Partial attempted
receipts must be reconciled before any rerun into a new directory.

The source and tests deliberately restrict the GitHub subprocess argv to
known read-only shapes. No repository source is checked out or executed.
The local progress frontier is not a true atomic GitHub snapshot: branch
heads may move during the batch and must be freshly revalidated when
opening a branch or evaluating a protected effect.

## Observed October 14 Lappy run

Live GitHub inventory: 95 accessible repositories, 90 nonarchived,
1 owner account-profile repository excluded, resulting in exactly 89
project repositories. The runner completed 89/89 read-only metadata tasks
with max_parallel=8, 0 HELD, 0 source executions, 0 GitHub mutations.
Separate per-project next-action plans were generated in local scratch.

The frontier classified 40 repositories with open PRs, 5 reporting a
latest-main CI failure, 20 without root CI configuration, 5 without a
root test directory, 10 without a recognized root manifest, and 9 with
foundational CI/test source needing a genuine worker route.

A separate reviewed Physica oscillator profile was actually executed
once (10 steps, VERIFIED_COMPLETE); this did not confer permission or
execution capability on the other 88 repositories.

Discovery's public-currentness CI is a genuine stale-census gate:
the public membership set differs from its September baseline.
The source refresh tool correctly refuses to overwrite evidence until
repository membership and visibility are reclassified. Do not suppress
the guard or falsely claim its CI is repaired.

## Hostile review — accepted

Metadata qualification and 89 action plans are not 89 application runs.
Existing open PRs may be delegated to other agents and must not be
blindly mutated. PORTAL still needs exact-source typed worker profiles,
per-project collision domains, authorized effect ceilings, independent
verification, execution logs, cancellation and resume after restart.
The current proof is valuable as a safe intake/qualification stage only.

This module and its tests are source-level candidates, not installation
of a new Windows PORTAL version and not an active host-composed worker route.
