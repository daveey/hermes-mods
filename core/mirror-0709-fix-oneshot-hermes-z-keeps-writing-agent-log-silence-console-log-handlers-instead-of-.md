# fix(oneshot): [daveey] hermes -z keeps writing agent.log; silence console log handlers instead of logging.disable

Patch 0709, source `8f03cbcee3b337ced4321f944adce42b31e81457`.

## Behavior

fix(oneshot): [daveey] hermes -z keeps writing agent.log; silence console log handlers instead of logging.disable. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _write_usage_file(path: Optional[str], result: dict, failure: Optional[str]`
- `def run_oneshot(`
- `+"""hermes -z keeps writing agent.log while keeping the console clean.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> logging.disable(CRITICAL) short-circuits isEnabledFor for every logger, so the
> setup_logging() file handlers saw nothing and one-shot turns left no
> conversation turn / API call #N / Turn ended lines in agent.log. Mute only
> console StreamHandlers (not FileHandlers, not the queued file handlers) and
> make logging.lastResort a no-op; stdout stays the reply, stderr stays clean.

## Code

- `hermes_cli/oneshot.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_oneshot_file_logging.py`

New or changed test definitions present in the source diff:

- `test_oneshot_logs_reach_agent_log_but_not_console`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
