# Kanban edit: lowering a card's priority needs --reason

Patches: 1601 1602

- `patches/1601-daveey-kanban-edit-refuse-to-lower-a-card-s-priority.patch`
- `patches/1602-kanban_db_edit-import-kanban_db-last-so-the-split-mo.patch`

Related: [mirror-0892-feat-kanban-edit-max-retries-goal-mode-goal-max-turns-max-runtime-seconds-session-id-.md](mirror-0892-feat-kanban-edit-max-retries-goal-mode-goal-max-turns-max-runtime-seconds-session-id-.md) (the operator fields this refusal must leave unapplied), [mirror-0802-fix-kanban-kanban-db-schedule-binds-kanban-db-last-so-it-imports-first-without-a-cycl.md](mirror-0802-fix-kanban-kanban-db-schedule-binds-kanban-db-last-so-it-imports-first-without-a-cycl.md) (same import-last fix and the test that guards it)

## Behavior

- **1601 (refuse silent lowering)** `hermes kanban edit <id> --priority N` with N below the stored priority and no
  `--reason` is refused with exit 1; the message names the current value and asks for `--reason` ("another actor
  may have raised it since you last read it"). Nothing is applied.
- **1601** With a non-blank `--reason` the lowering applies and the reason is recorded in the `reprioritized` event
  payload (`{"priority": N, "reason": "..."}`). A whitespace-only reason does not count.
- **1601** Raising, or re-setting the same value, needs no reason (payload `{"priority": N}` as before).
- **1601** A refused lowering combined with operator flags (e.g. `--max-retries`) leaves those operator fields
  unapplied too.
- **1601** `--reason` is now accepted with `--priority` as well as `--completion-contract`; with neither it is
  still refused (exit 2, message names `--priority`).
- **1601** Library default unchanged: `edit_task` without `require_lower_reason` still lowers silently. The
  commit says dashboard and REST callers don't opt in; only the direct `edit_task` call is tested, not those
  endpoints.
- **1602 (import cycle)** `hermes_cli.kanban_db_edit` can be imported before `kanban_db` without a circular
  `ImportError`.

## How it works

- **1601** `edit_task` moves from `kanban_db.py` to new `hermes_cli/kanban_db_edit.py` (keeps `kanban_db.py` under
  its line cap) and is re-exported from the tail of `kanban_db` together with new `PriorityLowerRefused(ValueError)`.
  New kwargs `priority_reason` and `require_lower_reason`. With `require_lower_reason=True` and a blank reason,
  inside `write_txn` it reads the stored priority and raises `PriorityLowerRefused` when the new value is lower, so
  a stale read can't slip past.
- **1601** `_cmd_edit` (`hermes_cli/kanban.py`) does a pre-check before the operator columns commit (so a refused
  edit applies nothing), then calls `edit_task(..., priority_reason=reason, require_lower_reason=True)` and turns
  `PriorityLowerRefused` into a `kanban edit: ...` error.
- **1601** `kanban_parser.py` help text for `--priority` and `--reason` documents the rule.
- **1602** 1601's `kanban_db_edit.py` imported `kanban_db as _kb` at the top while `kanban_db` imports
  `edit_task` back from it at its tail, so importing the split module first failed. 1602 moves
  `from hermes_cli import kanban_db as _kb` to the end of `kanban_db_edit.py`; all `_kb.` uses are inside
  `edit_task`, resolved at call time.

## Code

- `hermes_cli/kanban.py` (1601)
- `hermes_cli/kanban_db.py` (1601: `edit_task` removed, tail re-export added)
- `hermes_cli/kanban_db_edit.py` (1601 new; 1602 import moved last)
- `hermes_cli/kanban_parser.py` (1601)
- `tests/hermes_cli/test_kanban_edit_priority_lower.py` (1601, new)

## Tests

`tests/hermes_cli/test_kanban_edit_priority_lower.py` (1601; CLI cases go through the real argparse +
`kanban_command` dispatch):

- `test_lowering_without_reason_is_refused_naming_current_value`
- `test_lowering_with_reason_applies_and_records_reason`
- `test_blank_reason_does_not_count`
- `test_raising_needs_no_reason`
- `test_same_value_needs_no_reason`
- `test_refused_lowering_leaves_operator_fields_unapplied`
- `test_reason_without_priority_or_contract_still_refused`
- `test_library_default_unchanged_for_dashboard_and_rest` (direct `edit_task` call)

1602 adds no test. The existing `tests/hermes_cli/test_kanban_db_split_imports.py::test_split_module_imports_first`
(from patch 0802) globs every `hermes_cli/kanban_db_*.py` and imports each first in a fresh interpreter, so it
picks up `kanban_db_edit` automatically; that is the test the 1602 comment names.

These were not rerun by the mirror pass.

Untested:

- Dashboard and REST endpoints that call `edit_task` (only the library default is tested).
- The in-transaction refusal path in `edit_task` when the CLI pre-check passed but another writer raised the
  priority in between (race); the tests hit the CLI pre-check or the same check without a race.
- `--reason` combined with both `--priority` and `--completion-contract`.

## Applying

- Apply 1601 then 1602; 1602 only edits the file 1601 creates. Without 1602, importing `hermes_cli.kanban_db_edit`
  before `kanban_db` raises a circular `ImportError` (fails the split-imports test).
- Depends on the operator-field edit path (0892) and the `--reason` / `--completion-contract` edit path already
  in `_cmd_edit`.
- No config.

## Gotchas

- Any new `kanban_db_*` split module must import `kanban_db` at its end (or lazily), not at the top; 1602 repeats
  the 0802 fix for a new module.
- The guard is opt-in at the library level: a new caller that should be guarded must pass
  `require_lower_reason=True`.
- `PriorityLowerRefused` subclasses `ValueError`; a caller catching `ValueError` broadly will swallow it.

## Plugin?

No: it changes core `edit_task` transaction logic and the built-in `kanban edit` CLI, which have no plugin hook.
