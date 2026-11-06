# Catalog release identity

## Behavior

Patch 2018 (fork PR #910, source commit `d84d70b45253b673264f427dcc50ea089833be58`) makes the plugin-catalog CI
job resolve a real Hermes base version on the tagless fork:
- After the trusted base has been confirmed, the job fetches only the fixed official upstream release refs
  (`refs/tags/v*` from `https://github.com/NousResearch/hermes-agent.git`), never PR-controlled metadata.
- `hermes_cli.version_info` still selects only tags reachable from the trusted `HEAD`. A newer, unrelated
  upstream tag is fetched but cannot inflate the base version.
- A conflicting local tag is not forced: the fetch fails and the step fails, leaving the tag and `HEAD` untouched.
- With no reachable release tag the version stays `unknown`, and catalog validation remains fail-closed.
- No pin or confinement rules change.

## How it works

A new `Fetch upstream release identity` step in `.github/workflows/plugin-catalog-ci.yml`, gated on
`steps.changed.outputs.files != ''`, runs
`git fetch --no-tags --filter=blob:none <official upstream> 'refs/tags/v*:refs/tags/v*'` after
`Confirm the trusted checkout` and before `Prepare Hermes from the trusted base`. The refspec is non-forcing (no `+`), so an existing tag with a different target is
refused instead of moved, and the checked-out commit is never changed.

## Code

- `.github/workflows/plugin-catalog-ci.yml` (+12): the fetch step.
- `tests/ci/test_plugin_catalog_release_identity.py` (new, 52 lines): drives the workflow step's real `run` script.

## Tests

`tests/ci/test_plugin_catalog_release_identity.py` builds a real local upstream Git repository (release tag
`v0.23.0`, a later commit tagged `v99.0.0`), clones it without tags, detaches at the release commit and asserts the
version is `unknown`. It then runs the workflow step's exact script, redirecting the official URL to the local repo
with `url.<local>.insteadOf`:
- reachable tag: exit 0, `HEAD` unchanged, clean status, `v99.0.0` fetched but base version is `0.23.0`;
- conflicting tag (`v99.0.0` pre-pointed at the release commit): nonzero exit, tag and `HEAD` unchanged.

Executed in this mirror pass: 2 passed on the source tree and 2 passed on the replayed public tree; see
[verification](mirror-2018-verification.md). The real GitHub fetch and full plugin-catalog workflow also passed
on catalog-only proof PR #911 against the landed trusted base: isolated OMH registration and declared
capabilities matched at the unchanged pin. That proof PR was closed unmerged.

## Applying

Use the complete [README Applying recipe](README.md): patch 2018 requires the hash-pinned upstream integration
bridge `bridges/upstream-b45f3ad7cff-via-b447a1f13d2.diff` immediately before it, with the 2018 policy and replay
plan (exclusion base `a2db0efb2969d2527d27bf95a53066f5e064d9ad`).

## Gotchas

- Do not add `+` to the refspec or `--force`: forcing would let a fetched tag silently move an existing one.
- Fetching tags does not make a version: only tags reachable from the checked-out base count.
- The test imports helpers from `tests/ci/test_plugin_catalog_ci_confinement.py`; keep both together.

## Plugin?

No: this changes core CI workflow behavior, which a plugin cannot alter.
