# Watched Kanban tables refuse REPLACE writers

Mirror snapshot 4142; ordinals 4078–4080.

## Behavior

- A new source-tree test fails when production code contains `REPLACE INTO` or `INSERT OR REPLACE INTO` against any of seven watched Kanban tables: `tasks`, `task_links`, `task_runs`, `task_events`, `task_comments`, `task_chat_links`, `kanban_notify_subs`.
- The failure message lists `path:line` for each hit and tells the writer to use `INSERT ... ON CONFLICT(...) DO UPDATE` instead, citing the writer precondition in `hermes_cli/kanban_task_change.py`: REPLACE can delete another task's row without marking its change cursor.
- No production code or trigger semantics change; these entries only add tests.

## How it works

- 4078: `tests/hermes_cli/test_kanban_no_replace_guard.py` scans `hermes_cli`, `gateway` and `plugins`. Python files are parsed with `ast` and every string constant is checked (this folds adjacent literals and static f-string pieces and skips comments). `.sql`, `.js`, `.ts`, `.tsx`, `.jsx`, `.mjs` and `.cjs` files are scanned as raw text. Test files, `tests/` and `__tests__` directories, `node_modules`, `__pycache__` and `.git` are skipped. A regex treats SQL comments as whitespace between `INSERT`, `OR`, `REPLACE` and `INTO`, and accepts an optional schema prefix and bare, double-quoted, backtick-quoted or bracketed identifiers.
- 4079: For JS/TS sources, `\"` is unescaped before matching so escaped identifier quotes inside string literals are caught.
- 4080: Single-quoted identifiers are accepted, and whitespace or comments are allowed around the schema dot. A test confirms against an in-memory SQLite database that these forms really replace a row, so the guard is not matching invented syntax.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/hermes_cli/test_kanban_no_replace_guard.py`

Mail entries in the ordered series:

- 4078: [`13d4a0897cbf218b10f31540739dc372faafa123`](patches/4078-daveey-Guard-watched-kanban-tables-against-REPLACE-w.patch)
- 4079: [`846d67e798b771fa44d836417ce1cfbace36ee4c`](patches/4079-test-kanban-detect-escaped-SQL-identifier-quotes-in-.patch)
- 4080: [`9a06e40ed82b7b372491a75d3289af849b03c01d`](patches/4080-test-kanban-catch-SQLite-quoted-and-commented-identi.patch)

## Tests

All in `tests/hermes_cli/test_kanban_no_replace_guard.py`:

- `test_production_writers_do_not_replace_watched_tables` (4078): the real tree has no violations.
- `test_every_watched_table_is_rejected` (4078): each watched table with three REPLACE spellings, including newline-separated keywords.
- `test_quoted_schema_qualified_tables` (4078): schema-qualified names with double quotes, backticks, brackets and mixed case.
- `test_sql_comments_between_keywords` (4078): block and line comments between keywords.
- `test_safe_statements_are_allowed` (4078): `ON CONFLICT DO UPDATE`, `OR IGNORE`, a different table with a watched prefix, an unwatched table and the SQL `replace()` function are not flagged.
- `test_scan_finds_scratch_caller_and_excludes_tests` (4078): a temporary writer under each scanned root is reported twice (concatenated literal and f-string) and a `test_` file is ignored.
- `test_scan_covers_non_python_sources` (4078): a TypeScript template string is reported.
- `test_scan_escaped_quoted_identifiers` (4079): escaped double quotes in `.js`, `.ts`, `.mjs` for every watched table.
- `test_scan_sqlite_identifier_forms` (4080): single-quoted names and comment-separated schema dots in `.py`, `.sql`, `.js`, each first executed in SQLite to prove it replaces a row.

No terminal, execute_code, tool, REST or Desktop entry path is involved; this is a static scan test.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The test file itself states this is a static literal guard, not SQL data-flow analysis; dynamically assembled table names or keywords need review.
- Only the three listed source roots are scanned, and only the listed file suffixes.
- Non-Python files are scanned as whole text and reported at line 1.
- Test files are exempt, so counterexamples can live under `tests/`.

## Plugin?

No. It is a repository test guarding core writer code; a plugin cannot enforce what core modules may write.
