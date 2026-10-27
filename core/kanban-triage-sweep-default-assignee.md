# Kanban: default CLI assignee and stale-triage sweep

Patch **0452** (`34d27c11a0`).

## Behavior

`hermes kanban create` without `--assignee` now assigns `kanban.default_assignee` (config) or `default`, because an
unassigned ready/todo card is never spawned. Triage and blocked drafts stay unassigned. `sweep_stale_triage` moves
triage cards idle for 3600 s to `ready` on the default profile with a "specify or park with a probe" brief, once per
card (`triage_swept` event) so a worker that re-triages cannot loop. Cards with an open human request are skipped.

## Code

`hermes_cli/kanban.py` (`_cli_default_assignee`), `kanban_db.py` (`_default_ready_assignee`), `kanban_db_dispatch.py`
(`sweep_stale_triage`).

## Tests

`tests/hermes_cli/test_kanban_unclaimable_cards.py`.

## Applying

Independent of the routing series.

## Gotchas

Trackers for remote work should use `kanban_schedule` with a `wake_check`, not triage.

## Plugin?

No.
