# kanban_create refuses a second owner for work an open card already does

Patch: [`patches/0726-feat-kanban-kanban_create-refuses-a-second-owner-for.patch`](patches/0726-feat-kanban-kanban_create-refuses-a-second-owner-for.patch) (fork commit `d0cc66e9f4`); CLI/REST entry paths: [`patches/0729-feat-kanban-hermes-kanban-create-and-REST-POST-tasks.patch`](patches/0729-feat-kanban-hermes-kanban-create-and-REST-POST-tasks.patch) (fork commit `b3ae470c6d`)

## Why

A chat said "approve stage b" and created a "Stage B" card on the default board, while Stage B was already
running on the project's own board from another chat. The chat counted owners by its OWN chat links (only the
finished Stage A cards), and `kanban_create` only ever deduplicated an identical retry or a matching
`idempotency_key`, so it created the duplicate with no hint. A coordinator archived it two minutes later.

## Behavior

Before creating (and before placement routes the card to another gateway), `kanban_create` scans open cards
(todo, ready, running, scheduled, blocked, review, triage):

- on every board of this gateway, from the live board DBs (read-only), and
- on every other gateway, from the hermes-hub replica (`hub.db`, `HERMES_HUB_STATE`).

An open card matches when either:

- the new card names its board (the slug in the title, or `/<slug>` / `board <slug>` in the body, e.g. an
  `org/<slug>` repo), the new card is not being created on that board, and the two titles share a word; or
- the two titles share at least half of the longer title's significant words, 3 or more (stop words dropped).

Never a match: closed cards, the creator's own card, the new card's `parents`, cards the same chat created, and
cards the same worker created (fan-out).

On a match nothing is created. The result is `{"ok": false, "created": false, "code": "create_overlap",
"matches": [{id, gateway, board, status, title, chats, why}], "total_matches", "note"}` (up to 8 matches, best
first). The agent continues the existing card, or calls again with `distinct_from=[ids]` plus
`distinct_reason` for genuinely separate work. `distinct_from` without a reason is refused again.

Detection: every refusal and override writes a `create_overlap` event (`outcome` refused/override, matches,
reason, session). An override lands on the new card; a refusal lands on the creator's own card, else on the best
local match. The coach's `plugins/coach/create_overlap_watch.py` files one card per host and ISO week when more
than 2 overrides in 7 days end up archived (a duplicate let through).

The tool description says "count owners by board/project, not by your chat's links".

Every create entry path runs the same check (patch 0729):

- `kanban_create` tool: `distinct_from` + `distinct_reason`; the refusal is the tool result.
- `hermes kanban create` (terminal, execute_code, crons): `--distinct-from <id>` (repeatable) +
  `--distinct-reason`; a refusal exits 2, prints the refusal JSON on stdout with `--json`, else the note and
  matches on stderr, with a ready-to-paste `--distinct-from ...` retry hint.
- Dashboard/REST `POST /tasks`: JSON fields `distinct_from` + `distinct_reason`; a refusal is HTTP 409 with the
  refusal object as `detail`.

On every path an `idempotency_key` retry (a non-archived card with that key exists) skips the check, and a keyed
creator is never blocked by open cards from its own key family (the text before the first `:`, e.g.
`coach-fix:*`, `selfheal:*`): automated creators (coach and self-heal crons, open-asks, user-steps) dedupe by key.
Parents never match; courier-applied cards are not re-checked.

Off switch: `kanban.create_overlap_check: false`. A missing or unreadable replica or board is skipped and
logged; a crash in the check creates the card as before (never a block).

## Code

- `hermes_cli/kanban_overlap.py`: `find` (scan + match), `check` (gate used by the tool), `record` (event).
- `tools/kanban_tools.py` `_handle_create`: calls `kanban_overlap.check` (skipped for courier-applied cards,
  which the asking gateway already checked) and records an accepted override on the new card.
- `tools/kanban_tools_schemas.py`: `distinct_from`, `distinct_reason`, description line.
- `hermes_cli/config_defaults.py`: `kanban.create_overlap_check: True`.
- `hermes_cli/kanban.py` `_cmd_create` + `hermes_cli/kanban_parser.py` (0729): CLI gate and flags.
- `plugins/kanban/dashboard/plugin_api.py` `create_task` (0729): REST gate and body fields.
- `kanban_overlap.key_family` (0729): the idempotency-key family exemption, shared by all three paths.
- `tests/conftest.py`: `HERMES_HUB_STATE` points at a per-test dir so a host's live replica never leaks in.

## Tests

- `tests/tools/test_kanban_create_overlap.py` (tool handler, local boards): the incident shape (Stage B on the
  default board naming the project repo, while Stage B1 is open on the project's board from another chat) is
  refused naming B1 and its chat, and nothing is created (red before the fix); `distinct_from` without a reason
  is refused, with a reason it creates and records the override event; done/archived never match; same-board
  title overlap matches and an unrelated title does not; parents never match; the config switch turns it off; a
  board named in passing with an unrelated title is not a match; a placed create checks the replica first and
  never calls placement; a missing replica never blocks.
- `tests/hermes_cli/test_kanban_create_entry_parity.py::test_placed_tool_create_is_overlap_checked_before_it_leaves`
  (registry dispatch, placement route to another gateway): a duplicate never reaches the courier; distinct work is
  still placed.
- CLI entry path, `tests/hermes_cli/test_kanban_create_entry_parity.py`:
  `test_cli_create_refuses_a_duplicate_owner` (refused, `--json` and plain output, nothing created),
  `test_cli_create_override_is_accepted_and_recorded` (`--distinct-from` without a reason refused; with
  `--distinct-reason` created plus the override event), `test_cli_create_with_idempotency_key_skips_its_own_family`
  (key retry returns the card; same family not blocked; another family refused),
  `test_cli_create_never_matches_its_parent`. Red before 0729.
- REST entry path, same file: `test_rest_create_refuses_a_duplicate_owner` (409 `create_overlap`, nothing
  created), `test_rest_create_override_is_accepted_and_recorded` (409 without reason; 200 plus override event with
  it). Red before 0729.

## Applying

Applies on its own on top of the fork; it moves `_runs_where` and the marker-placement result fields into
`tools/kanban_tools_placement.py` (file-size offset).

## Gotchas

- Matching is lexical. A false match costs one extra call with `distinct_from`.
- Override events for a card placed on another gateway are logged only (the new card is not local).

## Plugin?

No: the gate sits inside the core tool's create path, before placement.
