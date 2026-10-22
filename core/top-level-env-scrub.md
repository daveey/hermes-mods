# Top-level launches drop the leaked agent-scope env

Patches:

- `patches/0169-fix-kanban-cards-never-park-forever-without-showing-.patch` (part 5): first version, kanban worker /
  delegate-child identity env only.
- `patches/0171-fix-env-top-level-launches-strip-the-whole-agent-sco.patch`: the whole agent-scope family, one shared
  list.

## Behavior

A Desktop, `serve`, `dashboard`, `gui`, `desktop` or `gateway` process started from a kanban worker or delegate child
(typically a worker's `hermes update` reopening Hermes.app with `open`) drops the inherited agent-scope env. Before
this, that Hermes.app carried `HERMES_DELEGATED_CHILD_CONTEXT` + `HERMES_KANBAN_*` (and later found
`HERMES_SESSION_ID/SOURCE`, `HERMES_SINGLE_QUERY_SESSION`, `HERMES_SUPERVISED_CHILD`), so every Desktop chat was
fenced as a delegate child and its kanban tools refused, and session identity leaked into every backend.

Stripping is gated on a worker marker: `HERMES_DELEGATED_CHILD_CONTEXT`, `HERMES_KANBAN_TASK`, `_WORKSPACE`, `_RUN_ID`
or `_CLAIM_LOCK`. Without one nothing is stripped, so a deliberate board pin (`HERMES_KANBAN_BOARD`) in an
operator's shell is untouched.

## How it works

- `agent/delegation_context.py` `strip_agent_scope_env`: removes exact keys `HERMES_DELEGATED_CHILD_CONTEXT`,
  `HERMES_SINGLE_QUERY_SESSION`, `HERMES_SPAWN`, `HERMES_SUPERVISED_CHILD` and the prefixes `HERMES_KANBAN_`,
  `HERMES_SESSION_`, `HERMES_CRON_`, `HERMES_PARENT_`, `HERMES_RPC_`, `HERMES_KERNEL_`. Operator tunables
  (`*_TIMEOUT`, `*_SECONDS`, `*_MAX_PARALLEL`) are kept. It sits next to `scrub_kanban_env`.
- `hermes_cli/top_level_env.py` (new): applied at CLI entry for the top-level subcommands, and `clean_launch_env()`
  builds the env for `main_desktop._relaunch_stopped_desktops` (the post-update `open`). `hermes_cli/main.py` calls it.
- `apps/desktop/electron/leaked-worker-env.ts`, run at Electron main startup (`main.ts`): the same list in
  TypeScript, logging the keys it dropped. A drift test pins it to the Python list.

## Code

`agent/delegation_context.py`, `hermes_cli/top_level_env.py`, `hermes_cli/main.py`, `hermes_cli/main_desktop.py`,
`apps/desktop/electron/leaked-worker-env.ts`, `apps/desktop/electron/main.ts`.

## Tests

- `tests/hermes_cli/test_agent_scope_env.py` (0171): `test_strip_agent_scope_env_removes_whole_family_keeps_tunables`,
  `test_strip_agent_scope_env_is_noop_without_worker_marker`, `test_relaunch_with_poisoned_environ_passes_clean_env`
  (Popen env), `test_backend_started_with_marker_is_not_a_delegate_child`,
  `test_chat_kanban_comment_succeeds_after_top_level_scrub` (end to end), `test_electron_mirror_matches_python_scope_list`.
- `tests/hermes_cli/test_kanban_park_invariants.py` (0169): `test_scrub_removes_leaked_worker_env`,
  `test_scrub_keeps_deliberate_board_pin_without_worker_marker`, `test_main_scrubs_for_top_level_surfaces`,
  `test_desktop_relaunch_after_update_gets_scrubbed_env`.
- `apps/desktop/electron/leaked-worker-env.test.ts`: drops a worker identity env; drops the whole family keeping
  tunables (0171); keeps a board pin without a marker.

## Applying

Apply 0169 then 0171 (0171 rewrites the helper 0169 added). Rebuild Desktop for the Electron half.

## Gotchas

- A worker's subprocesses carry `HERMES_DELEGATED_CHILD_CONTEXT` by design (`delegated_child_subprocess_env`, so
  descendants cannot mutate the board). Any top-level launch from a worker shell therefore needs this scrub; a new
  top-level subcommand must be added to the `top_level_env` list.
- The private hermes-sync tool mirrors the list in its `desktop_launch_env`; keep it in step when the list changes.
- Already-running Desktops started before the fix keep the leaked env until restarted.

## Plugin?

No. Process-launch environment is core startup code.
