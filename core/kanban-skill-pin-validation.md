# A pinned skill the assignee cannot load is refused at create

Patch **0681** (`45a6fbe63e`). Sits next to [kanban-managed-pin-policy.md](kanban-managed-pin-policy.md) (0670): that one refuses a model pin the registry cannot serve; this one refuses a skill pin the assignee cannot load.

## Behavior

- `kanban_create` and `hermes kanban create` refuse `skills` the assignee profile cannot load. Code `unknown_skill`, close-match suggestions, nothing created.
- The check uses the assignee profile, not the caller's.
- A dispatcher-owned worker whose pins all miss still starts. The missing names go into the prompt as a note instead of exiting rc=1. Interactive `hermes -s typo` still fails.
- A worker that dies with `Unknown skill(s)` is blocked `kind=assignment` on the first death. The block is sticky. It is not retried.

## How it works

`hermes_cli/kanban_skill_check.py` resolves skills with the same lookup the worker uses, against the assignee profile. `create_task(..., validate_skills=True)` is what the tool and the CLI pass. `cli.py` `finalize_preloaded_skills` treats a dispatcher-owned kanban worker differently from an interactive session. `kanban_db_dispatch.py` records an `Unknown skill(s)` death as `_DeadWorker.unknown_skill` and `_record_task_failure` uses `block_kind=assignment` instead of the crash retry.

## Code

`hermes_cli/kanban_skill_check.py` (new), `hermes_cli/kanban_db.py` (`create_task(validate_skills=)`), `tools/kanban_tools.py`, `hermes_cli/kanban.py`, `cli.py` (`finalize_preloaded_skills`), `hermes_cli/kanban_db_dispatch.py`.

## Tests

`tests/hermes_cli/test_kanban_create_skill_validation.py` (9):

- `test_tool_refuses_unknown_skill_and_creates_nothing`
- `test_tool_names_close_matches`
- `test_tool_accepts_a_skill_the_assignee_has`
- `test_tool_checks_the_assignee_profile_not_the_caller`
- `test_cli_refuses_unknown_skill_and_creates_nothing`
- `test_cli_accepts_a_real_skill`
- `test_kanban_worker_with_all_pins_missing_still_starts`
- `test_interactive_session_with_all_pins_missing_still_fails_loudly`
- `test_unknown_skill_exit_blocks_as_assignment_without_retry`

`tests/e2e/core/kanban/test_kanban_worker_contract.py`: the KNOWN gate for a worker dying on a missing pin is removed; that case now expects the worker to start.

This mirror did not re-run those files.

Untested: gateway and API-server create paths call `create_task` without `validate_skills=True`, so they are not checked. Dashboard `POST /tasks` is untested for skill pins.

## Applying

`git am -3` 0681. New module plus the create, CLI, worker-start and dispatch call sites.

## Gotchas

- A stale pin already in the DB is not rewritten. The worker starts with a prompt note; if it still dies with `Unknown skill(s)`, the dispatcher blocks it as assignment and does not retry.
- Close-match text is a suggestion only. The card is not created with the suggestion substituted.

## plugin?

No. Create-time refusal and the dispatcher block are core. A plugin cannot see the pin before `create_task` returns.
