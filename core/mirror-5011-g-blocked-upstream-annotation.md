# Session task cards: blocked-upstream annotation

Mirror entry 5010 (source subject: "annotate session cards with blocked ancestors").

## Behavior

For each card returned by the session task listing on a **local** board, the card detail pass may add a
`blocked_upstream` field: `{"id": <ancestor id>, "status": "blocked" | "triage"}`. It names the *nearest* ancestor
(by parent links) that is blocked or in triage, so a UI can say "this card is waiting on something stuck upstream".

- Only `blocked` and `triage` ancestors count. A `running`, `todo` or other ancestor never produces the field.
- Cards that are themselves `done`, `archived`, `blocked` or `triage` never get the field (any stale value is removed).
- Remote/replica rows that `session_tasks` appends later are unchanged: `_add_blocked_upstream` runs only on `select_listed_local_tasks` via `_read_session_tasks`. The new helpers themselves do not skip replica-shaped local rows.
- Failure is soft: if the link tables are absent (old schema) or SQLite raises, the card is left without the field and
  the failure is logged at DEBUG only. Nothing is raised to the caller.

## How it works

`_add_card_details` in `hermes_cli/kanban_session_activity_card_details.py` now ends by calling
`_add_blocked_upstream(conn, found)` after the machine/PR details are added.

- `_nearest_blocked(conn, task_id)` walks `task_links` (child -> parent, joined to `tasks` for status) **breadth-first**,
  one generation per loop, at most **20 generations** (depth cap 20). A `seen` set guards cycles.
- Within a generation, frontier children and parents are visited in sorted-id order and the first blocked/triage parent
  in sorted order wins, so ties at equal distance resolve to the lowest stable id. Because BFS is level-ordered, the first hit is the nearest.
- Terminal exhaustion of the frontier, or reaching the cap with no match, returns `None`.
- `_add_blocked_upstream` first `pop`s any existing `blocked_upstream`, skips the excluded statuses, wraps the walk in
  `try/except sqlite3.Error` and logs with `logging.getLogger(__name__).debug(..., exc_info=True)`.

## Code

- `hermes_cli/kanban_session_activity_card_details.py` (adds `import logging`, `_nearest_blocked`, `_add_blocked_upstream`).

## Tests

Source tests (read from the normalized patch, **not executed here**):

- New `tests/hermes_cli/test_kanban_blocked_upstream_details.py` on an in-memory two-table board:
  `test_chain_and_cycle` (blocked / triage / running ancestors in an a->b->c->a cycle), `test_nearest_ancestor`
  (triage beats a farther blocked), `test_excluded_card` (done/archived/blocked/triage cards get nothing),
  `test_depth_cap` (a card 20 hops from a blocked root is annotated, the one at 21 is not), `test_missing_links_fails_soft`
  (dropped `task_links`, debug log mentions blocked upstream).
- `tests/hermes_cli/test_kanban_session_activity.py`: `test_session_tasks_include_blocked_upstream` runs the real
  `session_tasks` against a full board with a cycle.

## Applying

Never apply this patch on its own. Follow the full segmented recipe in the core mods [README](README.md)
(section "Applying"); this entry only needs the earlier session-activity code that defines `_add_card_details` and `add_run_machines`.

This mail does not change Desktop/plugin rendering. Any consumer of `blocked_upstream` lives outside this patch.

## Gotchas

- The cap counts generations, not nodes; very wide graphs cost one query per frontier card per generation.
- Ancestors are located through local `task_links` only, so a blocker that exists solely on another board is invisible.
- Equal-distance ties are decided by id order, not by which link is older.
- The field is presentation evidence; do not use it for scheduling decisions.
