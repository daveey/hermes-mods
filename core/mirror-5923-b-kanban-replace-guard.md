# Kanban board connection factory: refuse SQLite REPLACE on task and run identities

Mirror snapshot 5923; ordinals 5867, 5868.

Entries:

- **5867**: fix(kanban): refuse REPLACE on tasks/task_runs at the board connection factory
- **5868**: fix(kanban): REPLACE guard tokenizes like SQLite; fixtures stop REPLACE-ing via the board

## Behavior

- SQLite `REPLACE` (`REPLACE INTO`, `INSERT OR REPLACE INTO`, `UPDATE OR REPLACE`) deletes conflicting rows implicitly, even with `recursive_triggers` off, so it bypasses the protected-row DELETE fences already on `tasks` and `task_runs`. The board connection factory now refuses such statements on those two tables with `ProtectedRowReplaceError` (a `sqlite3.IntegrityError` subclass) before SQLite can write or implicitly commit.
- The refusal applies to `execute`, `executemany` and `executescript` on the connection and on every cursor obtained from it (including custom cursor factories); a refused script writes nothing and does not commit a prefix, and a refused `executemany` consumes no parameter iterator.
- `REPLACE` and upserts on other tables are unchanged; `INSERT ... ON CONFLICT DO UPDATE` is the supported spelling for the two protected tables.
- 5868 makes the pre-flight lexer agree with SQLite's own tokenizer: a UTF-8 BOM where a token starts is whitespace (so `REPLACE INTO <BOM>tasks` is still refused), while a non-ASCII character inside an identifier is an identifier character (so `tasks` followed by a non-ASCII letter is a different table and is allowed).
- This is a public-factory invariant, not a sandbox: a caller that opens a raw `sqlite3` connection to the file is outside the guard (and trigger programs are out of scope).
- Test fixtures that used to `REPLACE` through the guarded board were rewritten: the operator-call replica copy now upserts, and four deliberate REPLACE adversarial tests write through a raw `sqlite3` writer so their safety claims still cover writers outside the factory.

## How it works

- `hermes_cli/kanban_replace_guard.py` tokenizes the SQL (comments skipped; quoted, backticked and bracketed identifiers unquoted and lowercased; 5868 widens the identifier class to non-ASCII and treats BOM/space/tab/LF/FF/CR as whitespace), finds `REPLACE INTO`, `OR REPLACE INTO` and `UPDATE OR REPLACE`, optionally skips a schema prefix (`main.tasks`), and raises when the target is `tasks` or `task_runs`.
- `ReplaceGuardMixin` composes outside custom connection factories: it pre-flights `execute`/`executemany`/`executescript` and routes them through a guarded cursor; `cursor(factory)` wraps any requested cursor class in a cached subclass that applies the same check.
- `hermes_cli/kanban_lockguard.py` makes its connection mixin derive from `ReplaceGuardMixin`; `hermes_cli/kanban_schema_owner.py` gained a read-only cursor mixin so schema-owner diagnostics also hold on cursor shortcuts.

## Code

Shipped non-test paths:

- `hermes_cli/kanban_replace_guard.py` (new)
- `hermes_cli/kanban_lockguard.py`
- `hermes_cli/kanban_schema_owner.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_kanban_replace_guard.py` (refusal per surface, table and verb; other-table REPLACE and upsert unchanged)
- `tests/hermes_cli/test_kanban_replace_guard_boundaries.py` (SQL spellings, script refusal commits nothing, bulk refusal consumes no parameters, custom cursor factories)
- `tests/hermes_cli/test_kanban_replace_protected_probes.py`
- `tests/hermes_cli/test_kanban_replace_public_trace.py` (the public lifecycle never issues REPLACE on protected tables; trace detector catches one)
- `tests/hermes_cli/test_kanban_replace_guard_lexer.py` (cases checked against real SQLite: BOM whitespace, non-ASCII suffix)
- fixture edits: `tests/hermes_cli/test_kanban_operator_call.py`, `test_kanban_sync_status_origin.py`, `test_kanban_task_change.py`, `test_origin_native_lifecycle.py`

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, plus any repair overlay the README names. They are not independent patches.

## Gotchas

- Any code (including tests) that REPLACEs into `tasks` or `task_runs` through the board connection now fails with `ProtectedRowReplaceError`; use an upsert.
- The guard only sees Python SQL entry points on the factory's connections and cursors.
- Keep the lexer in step with SQLite's tokenizer; the 5868 lexer test is meant to be compared against real SQLite behavior.

## Plugin

Core change; no plugin or hook is involved.
