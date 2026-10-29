# fix(hermes_cli): do not resolve another entry point's -p at import [daveey]

Patch 0838, source `1ccc80512eb199d012c34f1619f16ed219201aa4`.

## Behavior

fix(hermes_cli): do not resolve another entry point's -p at import [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `_startup_fast.ensure_project_root_on_path()`
- `def explicit_cli_profile() -> str | None:`
- `+"""Import-time -p/--profile scan for the Hermes CLI.`
- `profile name explains the rule; missing optional dependencies point at hermes`
- `def test_invalid_dash_p_after_a_subcommand_is_left_to_that_subcommand(monkeypatc`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Importing hermes_cli.main scanned sys.argv for -p/--profile and treated
> pytest's separate `-p usage_guard_probe` as a Hermes profile, then
> SystemExit'd the importer. Only a Hermes CLI process (console script or
> python -m hermes_cli.main) now owns that scan. Real -p/--profile selection
> is unchanged.
>
> Problem: importing hermes_cli.main under pytest with a separate
> `-p usage_guard_probe` raised FileNotFoundError then SystemExit(1)
> (26 setup errors; hermes_cli/main.py resolve_profile_env).
> Why 1: the scanner returned any token matching the profile-id regex.
> Why 2: the foreign-flag skip ran only when the value did not match that
> regex; `no:xdist` is skipped for the colon, `usage_guard_probe` is not.
> Why 3: the invocation check was consulted only on the invalid-name exit,
> and only asked whether argv[0] contained "pytest".
> Root (happened): import-time argv scan claimed profile-shaped -p without
> requiring the process to be the Hermes CLI.
> Root (not caught): the regression covered option-looking values only.
> Concatenated `-pusage_guard_probe` does not match the token-exact -p check.
> Recurrence: first hit of a profile-shaped plugin id.

## Code

- `hermes_cli/main.py`
- `hermes_cli/main_profile_argv.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_ux_messages_startup.py`

New or changed test definitions present in the source diff:

- `test_importing_main_with_foreign_separate_dash_p_does_not_resolve_a_profile`
- `test_apply_ignores_foreign_separate_dash_p_without_a_profile_lookup`
- `test_real_hermes_invocation_still_selects_dash_p`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
