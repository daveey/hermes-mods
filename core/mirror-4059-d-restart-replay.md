# Checkpoint restart waits, pause bounds, and replay retirement

Mirror snapshot 4059; covered ordinals: 3896, 3897, 3898, 3900, 3934.

## Behavior

- A managed-work-only core-update restart uses a two-second settle only for an intent with `kind == "unfenced"`, `checkpoint is True`, and `cron_cut is True`. Other shapes retain the fifteen-second wait; human or unclassified work alongside a card is not covered by either cap.
- A quota-wall reset remains trusted when the process merely has code skew. Only host-loop retirement indicates a successor may replay sooner.
- Test-only additions pin TUI pause freshness/config parsing and host-loop retirement races; they do not introduce new pause or lease behavior.

## How it works

`gateway/run_shutdown_restart_wait.py` re-reads the intent each poll and selects the shorter wait without changing the broader drain/stop bounds. Patch 3897 narrows the initial checkpoint-only predicate from 3896.

`tui_gateway/automation_replay.py:stale_code_reason` now consults only `host_loops_retired`, preserving the wall reset for the still-running skewed process. Retirement lets `trusted_resets_at` discard that reset for replay/close-out scheduling. Tests synchronize lease retirement between publishing its reason and releasing its locks, rather than assuming those events are simultaneous.

## Code

- `gateway/run_shutdown_restart_wait.py`
- `tui_gateway/automation_replay.py`

### Mirrored commits

- 3896: `b433d6c553f96058bf6866ae0f27d952c7ea8593` — Adds the short checkpoint settle for managed-only update waits.
- 3897: `95dbb119f19408e27d6ae0d908de18a57f80536a` — Restricts the short wait to unfenced boolean checkpoint/cron-cut intents.
- 3898: `eb641f4d19278f50249eaab87e2fff1535ee0497` — Pins TUI pause freshness and malformed configuration bounds.
- 3900: `d61aa27f727a44b2c8c06bca826caa6d874f6a36` — Keeps quota reset timing on code skew without host-loop retirement.
- 3934: `a4db3f50197c6fdfdcf72bb8e99ceb9556c19a41` — Pins host-loop retirement races and repeated skew confirmation.

## Tests

- `tests/gateway/test_core_update_kanban_wait.py`: intent-shape cases assert the exact expiration boundary, fifteen-second fallback, and exclusion of adjacent human work.
- `tests/gateway/test_instant_update_cron_drain_latency.py`: virtual-clock assertions pin the instant checkpoint settle to two seconds and prevent the cron stop floor from restoring spent grace.
- `tests/hermes_cli/test_turn_session_activity.py`: a pause exactly at freshness age still counts; one past it does not. Negative freshness floors at zero; invalid/unreadable config uses defaults; unparseable attempt limits retain the default cap.
- `tests/tui_gateway/test_auto_continue.py`: the producer still schedules an eligible first retry when `max_attempts` cannot be parsed.
- `tests/hermes_cli/test_superseded_host_loops.py`: skew without retirement retains replay and close-out reset times, whereas retired processes take the short delay. Event-gated races assert retired leases cannot remain exclusive or retain a newly won lock, and a single skew observation cannot satisfy the two-observation retirement threshold.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

A truthy value such as `1` is not the boolean checkpoint contract. Fenced intents do not get the short wait. Code skew is not proof that a successor will execute the replay. These timing and race assertions are local contracts, not end-to-end restart or delivery measurements.

## Plugin?

No. These decisions belong in core restart, replay, and lease ownership code; an observer plugin cannot safely replace their ordering.
