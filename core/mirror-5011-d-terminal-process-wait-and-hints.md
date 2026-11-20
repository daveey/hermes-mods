# Terminal: process wait gets its own window; wait_hint on sleep-polling

Patches: **4709, 4710, 4737**

- 4709 (`ca8e9eb361`): fix(tools): process wait gets its own 400 s window, descriptions stop recommending sleep [daveey]
- 4710 (`53c07f753c`): feat(tools): wait_hint on a foreground sleep that had a process or card to wait on [daveey]
- 4737 (`1d291c4030`): fix(tools): bound the wait hint's card-show flag loop and say "bounded", not "linear" [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- `process_manage(action="wait")` uses its own limit `TERMINAL_MAX_WAIT` (default the larger of 400 s and `TERMINAL_TIMEOUT`), held 20 s under the executor's per-call deadline; an omitted timeout waits the full window and still returns as soon as the process exits.
- Terminal and process_manage descriptions point at the wait rather than a foreground sleep.
- A foreground command that took 30 s or more, exited 0, and looks like a sleep or sleep-poll gets a `wait_hint` field when the task has a tracked running process (naming `process_manage wait`, with timeout no longer than the measured time) or the command reads one card's status (park/awaiting for workers, completion notice for chats). It only adds a field; nothing is refused or rewritten.
- The card-status scan is bounded to eight flags per `kanban` word within an 8,000-character scan.

## How it works

`tools/process_wait_limit.py` resolves the limit, used by `ProcessRegistry.wait` and the foreign wait; `tools/terminal_tool_wait_hint.py` implements the scan and is called from `_run_foreground` timing; prompt-surface fixtures re-rendered; env docs updated.

## Code

- `.env.example`
- `tools/process_registry.py`
- `tools/process_registry_foreign.py`
- `tools/process_wait_limit.py`
- `tools/terminal_tool.py`
- `website/docs/reference/environment-variables.md`
- `tools/terminal_tool_wait_hint.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/fixtures/prompt_surface/surfaces.json`: modified only
- `tests/fixtures/prompt_surface/tools/terminal.json`: modified only
- `tests/tools/test_process_wait_clarity.py`: modified only
- `tests/tools/test_process_wait_limit.py`: `test_default_is_400_and_ignores_terminal_timeout`, `test_env_override`, `test_invalid_override_falls_back_to_default`, `test_stays_under_the_executor_deadline`, `test_lower_configured_executor_deadline_lowers_the_wait`, `test_tiny_executor_deadline_never_reaches_zero`, `test_disabled_executor_deadline_leaves_the_limit`, `test_failing_executor_resolver_leaves_the_limit`, `test_raised_terminal_timeout_still_buys_a_longer_wait`, `test_real_executor_clamps_the_wait`, `test_wait_outlasts_terminal_timeout`, `test_wait_returns_as_soon_as_the_process_exits`, `test_requested_timeout_above_the_limit_is_clamped`, `test_default_window_is_the_limit` (+3 more)
- `tests/tools/test_terminal_tool_wait_hint.py`: `test_sleep_waits`, `test_not_sleep_waits`, `test_reads_one_card`, `test_not_a_status_read`, `test_two_cards_is_not_one_wait`, `test_card_id_in_a_path_is_a_workspace_not_a_status_read`, `test_a_workers_own_card_is_not_a_card_to_park_on`, `test_running_process_names_the_wait_call`, `test_the_offered_wait_is_no_longer_than_the_command_took`, `test_a_command_that_returned_quickly_gets_no_hint`, `test_newest_process_is_named_not_the_oldest`, `test_a_recognised_server_is_skipped`, `test_a_sleep_that_then_looks_elsewhere_gets_no_process_hint`, `test_a_service_probe_gets_no_process_hint` (+40 more)

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Prompt-surface snapshots must be regenerated when descriptions change.
- Recognised servers are skipped for process hints; a wrong hint is avoided over a missing one.
