# Skip unchanged notifier boards without suppressing recovery

Mirrored entries: 3852, 3853.

## Behavior

A notifier can avoid a writable board open and collection pass when the prior full pass proved nothing is pending and the complete recovery key is unchanged. In-flight recovery, GC, adapter/route changes and the periodic full-pass floor still force work. An unreadable probe falls back to the ordinary writable path.

## How it works

`notify_gate_probe` reads owned subscription count, global in-flight count and a composite key from query-only readers. The key combines the content revision with subscription bookkeeping aggregates that do not move that revision. The collector combines this with a serving key covering platform/profile ownership, adapters and routes.

A skip requires a recorded key, no in-flight rows, no GC due, and a full pass younger than 60 seconds. The key is recorded only if the pass lists no pending subscription; lack of an adapter, route or TUI owner therefore cannot turn a waiting row into an idle skip. `pending_kinds` narrows unseen-event tests to kinds the corresponding claim can consume. Skips count in loop accounting. The follow-up adds RuntimeError (including older Python symlink-resolution failures) to probe fallback errors.

## Code

- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban_db_notify.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/gateway/test_kanban_notifier_board_gate.py`: Checks writable-open counts settle on unchanged boards, content and bookkeeping writes reopen the gate, orphan/stale/foreign claims recover promptly, reconnects and route/TUI-owner availability deliver without a new event, GC and the full-pass floor still run, and skips are counted. Also tests pending-kind filtering, probe ownership counts and RuntimeError/OSError fallback to delivery.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The digest supplements the activity revision; it is not permission to ignore subscription bookkeeping. Any in-flight claim prevents skipping, including claims not owned by this notifier. A pass that left an unclaimed pending subscription is never cached as empty. No measured CPU saving or live delivery outcome is claimed here.

## Plugin?

No. Correct skips depend on claim filters, recovery clocks and board schema bookkeeping.
