# Parking progress reports and card-authored wake comments

Mirror entries: 4775, 4776.

## Behavior

Done-like reports placed in scheduling reasons reach the originating chat without declaring unfinished acceptance complete. Worker comments can satisfy a wake check by writing-card identity, and commenters receive a narrow warning when their comment will not wake the parked reader.

## How it works

Successful scheduling/time-block transactions atomically append a progress comment and park_progress_notice for recognized done-tail language. Existing gateway/TUI cursor delivery publishes it; explicit/inherited watches and validated card producers are exempt. Context includes at most the eight newest completed-parent handoffs, naming the older ones briefly. wait-comment --from-card uses writer_task event metadata or compatible relay-author spelling. The unmatched-comment note is emitted only where the card and its simple wait-comment-only alternatives are locally readable.

## Code

- `gateway/kanban_wake_fold.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_comment_origin.py`
- `hermes_cli/kanban_db_comment_ids.py`
- `hermes_cli/kanban_db_context.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_done_tail.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_timed_park.py`
- `tools/kanban_tools.py`
- `tui_gateway/session_notifications.py`
- `locales/*.yaml (notification catalog entries)`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Done-tail tests cover both parking operations, notice delivery, rollback, stale runs, watch/producer exemptions, long reports and incomplete acceptance. Comment-origin/tool tests cover writing-card metadata, relayed identities, UID safety and conservative probe parsing. Parent-cap tests cover small unchanged contexts and deterministic newest-parent selection.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_comment_origin.py`
- `tests/hermes_cli/test_kanban_context_parent_cap.py`
- `tests/tools/test_kanban_comment_writer_kind.py`
- `tests/tools/test_kanban_done_tail.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Progress is a report, not verified completion or an automatic watch conversion. Compound probes, pipes, raw SQL and unrelated commands are not judged by the warning. Older comments lack writer_task and can only match their author spelling.
