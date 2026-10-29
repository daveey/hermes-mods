# fix(kanban): module-form workers run with -P so a checkout workspace can't shadow the install [daveey] (#153)

Patch 0914, source `648e72d2ecbc20d12328e5945da008da488c87bd`.

## Behavior

fix(kanban): module-form workers run with -P so a checkout workspace can't shadow the install [daveey] (#153). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _launch_external_cron_worker(job: dict) -> bool:`
- `def _deliver_to_bot_chat(job: dict, content: str, profile: str, *, deferred: Opt`
- `def _committed_dependency_site_packages(project_root: Path) -> Path | None:`
- `def _rotate_worker_log(`
- `def _propagate_module_import_root(cmd: list[str], env: dict[str, str]) -> None:`
- `def forward_argv(task, board: Optional[str], worker_cmd: list) -> Optional[list]`
- `def test_cli_keeps_discovered_home_when_launch_selection_changes(tmp_path, monke`
- `def test_deliver_runs_canonical_bot_chat_lane():`
- `def test_resolve_hermes_argv_prefers_module_form_over_path_shim(monkeypatch):`
- `def test_resolve_hermes_argv_module_actually_runs():`
- `def test_kanban_create_with_parents_creates_a_local_deferred_card(mac):`
- `+"""Module-form Hermes children import the install's tree, never one in their cwd.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): module-form workers run with -P so a checkout workspace can't shadow the install [daveey]
>
> A hermes-agent worktree card spawns its worker as `python -m hermes_cli.main`
> with cwd = the worktree. -m puts the cwd first on sys.path, ahead of the
> install-root PYTHONPATH pin, so the worker imported the worktree's hermes_cli,
> took it as project root and refused: "no dependency environment is committed
> for this install". Every worktree card on the repo died at spawn.
>
> Module-form argv now carries -P (Python >= 3.11, the project floor) via
> cron.scheduler_worker_env.hermes_module_argv, on every entry path that pins
> the install tree on PYTHONPATH: kanban worker (_module_hermes_argv), the
> placement forwarder, the cron external worker and the cron bot-chat turn.
> is_hermes_module_argv recognises both shapes for the pin checks. The flag is
> not inherited, so workers' own python children keep cwd-first semantics.
> gateway.run._resolve_hermes_bin is left alone: its children get no pin and
> run from HERMES_HOME / the project root, where the cwd entry is what imports.
>
> Tests: tests/hermes_cli/test_worker_argv_ignores_cwd_checkout.py spawns each
> path's real argv+env from a decoy checkout (4 RED on the old argv: DECOY
> imported from the cwd) plus a grandchild-inheritance check.
>
> * chore(kanban): keep the -P argv change line-neutral for the code-health ratchet [daveey]
>
> * fix(ci): document kanban.create_overlap_check; give LSP spawn test's fake proc a pid [daveey]
>
> Both fail on origin/main 6db9b6d0bb (docs drift ratchet from d0cc66e9f4;
> _FakeProc lacked .pid after 47ae53b158 started reading proc.pid in _spawn).
>
> * chore: drop CI-drift fixes now duplicated on main [daveey]

## Code

- `cron/scheduler.py`
- `cron/scheduler_delivery.py`
- `cron/scheduler_worker_env.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_placement_forward.py`

## Tests

Source test files changed by this commit:

- `tests/cron/test_bot_chat_cli_home.py`
- `tests/cron/test_cron_bot_chat_delivery.py`
- `tests/hermes_cli/test_kanban_db.py`
- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_worker_argv_ignores_cwd_checkout.py`

New or changed test definitions present in the source diff:

- `test_kanban_worker_in_a_checkout_workspace_imports_the_install`
- `test_placement_forwarder_and_its_worker_import_the_install`
- `test_cron_external_worker_argv_imports_the_install`
- `test_cron_bot_chat_turn_argv_imports_the_install`
- `test_safe_path_flag_is_not_inherited_by_grandchildren`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
