# kanban-db-hint

When a Kanban tool refuses a board ("Refusing to open corrupt kanban DB …", or the newer
`KanbanDbUnreliableViewError` wording: "attached to a deleted WAL/SHM generation", "integrity probe disagrees with
an independent process"), this `transform_tool_result` hook adds one `hint` field to the result:

1. Check from a fresh process: `sqlite3 -readonly <db> 'pragma quick_check'` (read-only, no heredoc, so no approval
   wait).
2. If it prints `ok`, the board is fine. This backend has a stale view, so every `kanban_*` tool in the session fails
   until the backend restarts, `kanban_create` included. `hermes kanban show/list` in the terminal still works. The
   hint names the owner cards (if configured) and asks for the restart with `action_request`.
3. If it doesn't print `ok`, the damage is real. Don't REINDEX/VACUUM/repair the live file; report and ask for recovery.

The hook only matches results of `kanban_*` tools (or the `tool_call` bridge) that contain that error text. It
runs once per result (skips results that already carry the hint), and it never changes the original error.

Why: a chat whose backend held a stale board view could only report "the board is corrupt". The error gave it no
next step, and its own `kanban_create` failed the same way. The coach plugin's report_only nudge now also skips
"kanban_create" when the session's Kanban tools are down (see `plugins/coach`).

## Config (optional)

`<HERMES_HOME>/kanban-db-hint.json`:

```json
{"owner_cards": ["t_..."]}
```

Cards that own the stale-view fix. The hint names them so the agent points at them instead of opening a new card.

## Tests

```
cd plugins/kanban-db-hint
uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```

## Undo

`hermes plugins disable kanban-db-hint`
