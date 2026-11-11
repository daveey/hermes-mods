# Linked-card strip: which machine a card runs on

Patch **2152**.

## Behavior

Each card returned for a chat's linked-card strip (local board and replica) gets a display-only `run_machine` field so the Desktop can show whether it runs on the hub or a worker. Rules, in order: a running card or one with an unexpired lease runs on its `lease_holder`; a single `machine:<m>` pin in `runs_on` means `<m>`; a `worker-first-v1` placement means `worker-pool`; other `runs_on` labels (or an explicit empty list) name no single machine, so the field is omitted; otherwise `created_on`.

## How it works

New `hermes_cli/kanban_run_machine.py`: `run_machine(row, now)` implements the rules (lease expiry compared in ms); `placement_select` builds a SELECT list that yields `NULL` for columns an older board lacks; `add_run_machines(conn, found)` stamps cards from their task rows and swallows `sqlite3.Error` (a failed read stamps nothing). `kanban_session_activity_card_details._add_card_details` calls it for local cards; `kanban_chat_links._read_replica_links` and `replica_cards` select the placement columns and stamp replica cards.

## Code

- `hermes_cli/kanban_run_machine.py` (new), `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_session_activity_card_details.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

`tests/hermes_cli/test_kanban_run_machine.py`: `test_run_machine`, `test_add_run_machines_tolerates_old_boards`, `test_session_tasks_stamp_run_machine`, `test_replica_cards_stamp_run_machine`. Desktop rendering of the field is not covered by these tests.

## Applying

Apply ordinal 2152 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as an independent patch. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Display only: the Board decides placement, so the field can briefly disagree with a just-changed lease.
- Missing placement columns are selected as `NULL`; an older board can still fall back to `created_on`. The field is omitted when neither usable placement nor origin information exists.

## Could this be a plugin?

No. The data comes from core task columns through core session-activity and chat-link readers.
