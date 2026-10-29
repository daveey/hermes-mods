# fix(ci): scripts/check hooks and prompt-surface run on macOS pm installs

Patch 0910, source `5205b04e751f262c6a48f6c35a8bd11bbd282df2`.

## Behavior

fix(ci): scripts/check hooks and prompt-surface run on macOS pm installs. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `for root in "$repo" "$common"; do`
- `def _pin_environment() -> None:`
- `def render(out: Path) -> int:`
- `TMPDIR sat under a real directory with an AGENTS.md (the hub: ~/.hermes/cache/`
- `def test_check_reports_stale_files(tmp_path):`
- `def test_hooks_prefer_the_repo_virtualenv_over_path_python(tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The pre-push hook refused every push from the MacBook: prompt-surface could not render there.
> - _pin_environment set sys.platform='linux' before sysconfig/psutil loaded, so macOS looked up
>   _sysconfigdata__linux_darwin and psutil's Linux extension: load both under the real platform first.
> - render() replaced PYTHONPATH with the tree, dropping a pm install's dependency site-packages:
>   keep the caller's entries after the tree.
> - /var/folders -> /private/var/folders: the child scrubbed the resolved HOME from text carrying the
>   unresolved one; render under a resolved temp dir.
> - hook template: the Macs' venv/ predates the pm-managed deps; take PATH/PYTHONPATH from the main
>   checkout's composed pm environment (read-only), minus the main checkout itself.
>
> Tests: tests/ci/test_prompt_surface_hermetic.py (symlinked TMPDIR, PYTHONPATH, platform modules),
> tests/scripts/test_code_health_cli.py (pm-composed hook env); red before, green after on macOS.

## Code

- `scripts/check`
- `scripts/ci/prompt_surface.py`

## Tests

Source test files changed by this commit:

- `tests/ci/test_prompt_surface_hermetic.py`
- `tests/scripts/test_code_health_cli.py`

New or changed test definitions present in the source diff:

- `test_render_is_the_same_when_tmpdir_is_reached_through_a_symlink`
- `test_render_child_keeps_the_callers_pythonpath_after_the_tree`
- `test_pinned_render_keeps_the_hosts_platform_modules`
- `test_hooks_take_the_pm_composed_environment_minus_the_main_checkout`
- `test_check_refuses_old_python_before_importing_anything`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
