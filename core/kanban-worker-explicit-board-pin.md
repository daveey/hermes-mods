# A dispatcher worker's explicit board must match its pinned database

Patch: **1640** (`0c91f347dd`, `fix(kanban): refuse explicit worker board mismatches`).

Related: [mirror-0942-fix-kanban-a-plain-word-board-slug-is-not-a-board-reference.md](mirror-0942-fix-kanban-a-plain-word-board-slug-is-not-a-board-reference.md).

## Behavior

- A dispatcher worker runs with `HERMES_KANBAN_TASK` and a pinned `HERMES_KANBAN_DB`. When it passed an explicit
  `board=` to a Kanban tool naming another board, the call silently used the pinned board instead. Now such a call is
  refused with code `board_pinned` and fields `requested_board` / `pinned_board`: "Board '<x>' refused: this worker is
  pinned to board '<y>' and cannot reach other boards. Omit board or use its pinned board. Nothing changed."
- Naming the pinned board explicitly, or omitting `board`, works as before.
- The pinned slug is derived from the database path (`board_slug_for_path`), not from `HERMES_KANBAN_BOARD`, so a stale
  current-board hint cannot override the pin. The lexical path is used, so a board directory that is a symlink keeps its
  `boards/<slug>` name.
- Outside a worker (no `HERMES_KANBAN_TASK` or no DB pin) an explicit board is honored as before.

## How it works

`tools/kanban_tools_board.py` (new) holds `tool_board(board, quiet_close=False)`, the context manager every Kanban tool
already used as `_board`; it now calls `_check_explicit_worker_board(board)` first and raises `BoardPinnedError`
(a `ValueError` with `code = "board_pinned"` and `tool_error_fields`). `tools/kanban_tools.py` imports it as `_board`.
The tool schema's `board` description documents the refusal.

## Code

`tools/kanban_tools_board.py` (new), `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`.

## Tests

`tests/tools/test_kanban_explicit_board_pin.py::test_explicit_board_pin` (not rerun by this mirror pass), parametrized
over the worker's own board `default`, `team` and a symlinked `linked` board (POSIX only), through the real tool
registry: `kanban_create` and `kanban_show` with another board return `board_pinned` and change no rows; the pinned board
works; without `HERMES_KANBAN_TASK` the other board is written.

Untested: the other Kanban tools that go through `_board` (only `kanban_create` and `kanban_show` are called), and the
CLI (`hermes kanban --board`), which this patch does not change.

## Applying

Apply in series order. No config.

## Gotchas

The schema text says existing exact-target owner-wake grants are excepted. `_check_explicit_worker_board` has no such
branch, so the exception holds only where those grants do not go through `tool_board`; this patch does not test it.

## Plugin?

No. Board resolution for core Kanban tools.
