# fix(hermes_cli): honor -p from the python -c source launcher [daveey]

Patch 0840, source `06f3498b163fb79c896a6f2a92803cea39825e9e`.

## Behavior

fix(hermes_cli): honor -p from the python -c source launcher [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def explicit_cli_profile() -> str | None:`
- `def _looks_like_hermes_invocation() -> bool:`
- `print("OK")`
- `def test_apply_ignores_foreign_separate_dash_p_without_a_profile_lookup(tmp_path`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Problem: hermes-hub skills-sync at 2026-10-28T23:42Z printed
> "config set failed" for profiles bb-tuner and coach. hermes -p
> profile exits 2: the profile name is not a hermes command.
> Why 1: _apply_profile_override did not consume -p, so argparse took
> the profile name as the command.
> Why 2: _looks_like_hermes_invocation() is false when argv[0] is -c.
> Why 3: the POSIX launcher is python -c and rewrites argv[0] only
> after importing hermes_cli.main, which scans -p at import.
> It does set sys._hermes_pin_default_home before that import.
> Root (happened): 1ccc80512e required a hermes/main.py argv[0] and did
> not treat the source launcher (-c plus the pin flag) as the CLI.
> Root (not caught): the regression covered pytest argv[0] and
> python -m, not the generated launcher.
> Recurrence: first hit, six minutes after 1ccc80512e.
>
> Both argv[0] == -c and the pin flag are required. hermes --run-module
> leaves the flag set after runpy replaces argv[0]; that -p stays foreign.

## Code

- `hermes_cli/main.py`
- `hermes_cli/main_profile_argv.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_ux_messages_startup.py`

New or changed test definitions present in the source diff:

- `test_pin_flag_without_dash_c_argv_does_not_select_dash_p`
- `test_source_launcher_python_dash_c_still_selects_dash_p`
- `test_importing_main_from_source_launcher_consumes_dash_p`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
