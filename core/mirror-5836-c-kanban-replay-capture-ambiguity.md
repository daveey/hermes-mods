# Kanban sync: retain pending capture ambiguity without certifying history (mirror 5727–5729)

## Behavior
- Pending capture ambiguity is retained rather than certified as history; nullable pending identities are tainted by physical row.
- Idle-round tests separate WAL poll reuse from checkpoint pager refresh.

## How it works
`kanban_sync_capture_storage.py` with hooks in `kanban_db_sync.py`; behavior documented in `docs/kanban-replay-capture-storage.md`.

## Code
- `hermes_cli/kanban_sync_capture_storage.py`
- `hermes_cli/kanban_db_sync.py`
- `docs/kanban-replay-capture-storage.md`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_kanban_sync_capture_storage.py` checks unknown defaults, immutable original tuples, sticky taint, rollback, delete/reinsert, and NULL-key rowid targeting (`test_null_identity_change_taints_exactly_the_changed_row`). `test_kanban_sync_idle_round.py` separates post-checkpoint refresh from subsequent WAL poll reuse. The tests do not establish certified capture activation or old-writer admission (untested here and separate prerequisites).

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Ambiguous rows must not be promoted to certified history.

## plugin?
No. Core change.
