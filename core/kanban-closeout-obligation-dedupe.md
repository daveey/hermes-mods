# Kanban: closeout obligations counted once

Patches: **1744** (`5e059f005f`, `fix(kanban): deduplicate remote obligations from closeout counts [daveey]`); **1773** (`508014e752`, `fix(kanban): deduplicate board-qualified local closeout obligations [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A remote obligation already counted is not counted again in closeout (1744).
- A board-qualified local closeout key (`default/t_b`) matches the bare open id (`t_b`), so a human-blocked local card is shown once, not as both blocked and active (1773).

## How it works

`hermes_cli/kanban_activity_identity.py` normalizes card keys before `annotate_kanban_activity` compares them.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1744: (subject only)
- 1773: Problem: a chat whose settled batch holds a human-blocked local card painted it twice: kanban_blocked=1 AND kanban_active=1 (grey + red), kanban_waiting_on=['default/t_b','t_b']. Reproduced with the real chat-closeout core.card_key over core's session_tasks rows.

## Code

`hermes_cli/kanban_activity_identity.py`, `hermes_cli/kanban_session_activity.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_local_waiting_overlap.py`: `test_legacy_bare_closeout_id_still_deduplicated`, `test_lineage_dedup_keeps_same_id_on_two_boards`, `test_local_and_remote_overlaps_together`, `test_local_closeout_overlap_counts_once`, `test_named_board_overlap_counts_once`, `test_same_task_id_on_another_board_is_not_conflated`
- `tests/hermes_cli/test_kanban_remote_waiting_overlap.py`: `test_remote_closeout_overlap_counts_once`, `test_remote_waiting_identity_does_not_collapse_another_gateway`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
