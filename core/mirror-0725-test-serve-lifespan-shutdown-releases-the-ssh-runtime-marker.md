# test(serve): lifespan shutdown releases the SSH runtime marker [daveey]

Patch 0725, source `74f0b74178e3a50fe56bff7371b7e19648745600`.

## Behavior

test(serve): lifespan shutdown releases the SSH runtime marker [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_prune_removes_dead_serve_markers_and_leaves_live_ones(tmp_path, monkeyp`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The atexit path was already covered. This drives FastAPI lifespan shutdown
> and fails if that finally block stops calling _release_ssh_runtime_marker.

## Code

- `tests/hermes_cli/test_ssh_ownership_endpoint.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_ssh_ownership_endpoint.py`

New or changed test definitions present in the source diff:

- `test_lifespan_shutdown_calls_release_ssh_runtime_marker`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
