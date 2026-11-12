# Upstream sync landing PR lookup and runner evidence

Mirror entries: [2255](patches/2255-daveey-upstream-sync-gate-find-the-landing-PR-by-head-ref-not-the-origin-slug-s-owner.patch), [2270](patches/2270-fix-ci-accept-migrated-Blacksmith-sync-suite-evidence-daveey.patch), [2272](patches/2272-refactor-ci-table-drive-sync-evidence-rejection-fixtures-daveey.patch).

## Behavior

- Reuse an open main-target landing PR by head ref even when the origin slug's owner is stale after a repository transfer.
- Prefer an exact tested-head match; still refuse a PR whose head differs from the tested candidate.
- Accept full-suite evidence from approved self-hosted or migrated Blacksmith runners, with a nonempty runner name; preserve required-job and exact-head checks.

## How it works

_land_pr pages open PRs with base=main, filters on the staged branch's head ref and chooses the newest exact-head candidate (or newest branch match for the existing mismatch refusal). The evidence predicate accepts the approved Blacksmith label in addition to self-hosted. 2272 table-drives negative evidence fixtures without changing runtime policy.

## Code

- `scripts/ci/upstream_sync.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/upstream_gate_fake.py`
- `tests/scripts/ci/test_upstream_sync.py` — `test_an_open_landing_pr_is_reused_when_origin_names_a_transferred_repo`, `test_migrated_blacksmith_full_suite_can_land`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Finding a PR does not authorize an untested head, bypass branch protection, or prove CI was rerun by this mirror. Runner migration is a narrow label allowance, not acceptance of arbitrary hosted jobs.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
