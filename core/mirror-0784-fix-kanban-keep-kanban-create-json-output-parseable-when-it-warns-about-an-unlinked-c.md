# fix(kanban): keep `kanban create --json` output parseable when it warns about an unlinked card [daveey]

Patch 0784, source `a9a6fa2de60a1ed1a5e54415b1224c464607b481`.

## Behavior

fix(kanban): keep `kanban create --json` output parseable when it warns about an unlinked card [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_create(args: argparse.Namespace) -> int:`
- `def run_slash(rest: str) -> str:`
- `_HERMES_BEHAVIORAL_VARS = frozenset({`
- `def test_cli_block_agent_owned_allowed_with_wake_check(kanban_home):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> run_slash appended create's stderr "not linked to any chat" warning after
> the JSON object, so json.loads failed (test_cli_create_runs_on, run from an
> agent shell). --json now returns stdout only via run_slash; create carries
> the warning in a "warning" field (and still prints it to stderr on the CLI).
> The hermetic test env also drops HERMES_SESSION_ID/HERMES_RPC_SOCKET/
> HERMES_KERNEL_SENTINEL, the agent-invoked markers it still leaked.

## Code

- `hermes_cli/kanban.py`

## Tests

Source test files changed by this commit:

- `tests/_fixtures/env_filter.py`
- `tests/hermes_cli/test_kanban_cli.py`

New or changed test definitions present in the source diff:

- `test_create_json_via_slash_is_pure_json_with_unlinked_warning_field`
- `test_create_json_cli_keeps_stdout_pure_and_warns_on_stderr`
- `test_create_text_mode_still_shows_unlinked_warning`
- `test_create_json_without_warning_has_no_warning_field`
- `test_hermetic_env_drops_agent_session_markers`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
