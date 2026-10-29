# fix(ci): scripts/check hook borrows the installed Hermes env in a clone that is not an install

Patch 0911, source `fb1ec372828bbad941922e70efc3e9649003fa95`.

## Behavior

fix(ci): scripts/check hook borrows the installed Hermes env in a clone that is not an install. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `done`
- `def test_hooks_prefer_the_repo_virtualenv_over_path_python(tmp_path):`
- `def test_hooks_take_the_pm_composed_environment_minus_the_main_checkout(tmp_path`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> ~/code/hermes (the sync mirror, branch state) is a sanctioned place for origin/main worktrees but
> has no pm install and its .venv has no dependencies, so prompt-surface refused every main push from
> it. When the main checkout has no pm/environments.py, compose from ${HERMES_HOME:-~/.hermes}/hermes-agent;
> skip a composition that carries no dependency path.
>
> Test: tests/scripts/test_code_health_cli.py::test_hooks_take_the_pm_composed_environment_minus_its_checkout[main_checkout|installed_hermes].

## Code

- `scripts/check`

## Tests

Source test files changed by this commit:

- `tests/scripts/test_code_health_cli.py`

New or changed test definitions present in the source diff:

- `test_hooks_take_the_pm_composed_environment_minus_its_checkout`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
