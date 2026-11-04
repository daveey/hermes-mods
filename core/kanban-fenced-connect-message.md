# Kanban: a fenced connect of a missing board DB names the fence

Patches: **1881** (`ba48afaefc`, `[daveey] fix(kanban): a fenced connect() of a missing board DB names the fence`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A delegated child whose inherited read-only fence covers a missing board DB path now gets an error naming the fence instead of SQLite's bare "unable to open database file".

## How it works

`hermes_cli/kanban_db_connect.py` checks existence before the `?mode=ro` open under a fence.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1881: A spawned descendant whose HERMES_DELEGATED_CHILD_CONTEXT root covers the board path opens it ?mode=ro. When the file did not exist SQLite only said "unable to open database file" (raised from kanban_lockguard), which a worker misread as a lockguard bug (t_01a19efdde6364d6; real cause was the inherited fence covering a pytest tmp_path under <root>/cache/scratch, t_01a19f60a4e241d2).

## Code

`hermes_cli/kanban_db_connect.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_delegate_lineage_fail_closed.py`
- `tests/hermes_cli/test_kanban_fenced_missing_board.py`: `test_fenced_connect_of_missing_board_names_the_fence`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
