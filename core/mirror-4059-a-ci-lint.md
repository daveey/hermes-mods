# Advisory lint fetch recovery on partial clones

Mirror ordinals: 3737, 3738.

## Behavior

- Profile-scope and public-surface advisory steps use the shared merge-base fetch helper rather than direct deepen fetches on a promisor clone.
- They compare the actual PR head SHA against the fetched base, avoiding dependence on a synthetic merge commit.
- If both fetch attempts fail, one visible warning marks a skipped advisory rather than silently implying it ran.

## How it works

The workflow passes `BASE_REF` and `PR_HEAD_SHA` to `fetch-merge-base.sh`, which already owns the filtered-fetch/full-history fallback and partial-clone configuration restoration. Both steps retain continue-on-error and their bounded step timeout. The follow-up modifies only the test shim's healthy mode: if the installed Git itself hits the known promisor bug, that shim temporarily removes promisor configuration and strips the filter for the simulated healthy fetch, then restores it.

## Code

- `.github/workflows/lint.yml`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/ci/test_lint_advisory_fetch.py` — The real workflow step scripts run on disposable shallow/partial clone fixtures. Assertions establish filtered then unfiltered retry order, correct checker argv and three-dot diff/blob reads, a refreshed base ref, restored partial config, a one-fetch healthy path, and exactly one warning with no checker invocation when all fetches fail.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Advisory success is not evidence that a skipped check inspected code. The healthy-path shim workaround is test harness behavior, not an extra production bypass.

## Plugin?

No. GitHub Actions workflow behavior cannot be supplied by an installed runtime plugin.
