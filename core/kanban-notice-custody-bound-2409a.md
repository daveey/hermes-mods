# Bounded live Kanban notice custody

Mirror entries: [2281](patches/2281-fix-kanban-bound-live-notice-custody-independently-of-admission-daveey.patch), [2282](patches/2282-docs-kanban-correct-preserved-release-diagnostic-description-daveey.patch), [2283](patches/2283-test-tui-bind-custody-helper-in-viewer-poller-fixture-daveey.patch).

## Behavior

- Bound undelivered live-process notice custody independently of whether chat admission is open.
- Measure elapsed age monotonically after initial observation; partial acknowledgements and added ranges do not renew the oldest outstanding claim.
- Expiry drops stale in-memory copies and releases only the exact still-owned stale durable marker, never a newer reclaim of the same range.
- Deduplicate reclaimed text against delivered and buffered event keys; preserve best-effort poller-stop release diagnostics.

## How it works

kanban_notify_clock keys process-local clocks by database/subscription and marker identity. Foreign/legacy claims bootstrap age from persisted time; own fresh claims start from a local monotonic clock. Recovery checks both dead and expired holders, including idle boards, and compare-and-swap rewind fences the full marker/cursor snapshot. kanban_claim_custody checks buffers before admission-dependent paths; local expiry uses release_expired_notify_claim, remote expiry delegates release, and durable claims are not expired as undelivered text. 2282 corrects exception comments without changing behavior; 2283 binds the extracted helper in a viewer-poller fixture.

## Code

- `hermes_cli/kanban_db_notify.py`
- `hermes_cli/kanban_notify_clock.py`
- `tui_gateway/kanban_claim_custody.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_notify_clock_bound.py` — `test_fresh_own_claim_then_idle_expiry_ignores_wall_clock`, `test_partial_ack_and_additional_claim_preserve_oldest_age`, `test_completed_marker_cleans_clock_and_new_claim_is_fresh`, `test_prune_deleted_subscription_preserves_active_age` (additional cases in this file).
- `tests/tui_gateway/test_kanban_notice_claim_bound.py` — `test_wall_clock_jump_does_not_expire_buffer`, `test_sweeper_reclaim_of_buffered_range_is_delivered_once`, `test_sole_live_claimer_recovers_without_new_events`, `test_buffered_notice_returns_claim_while_admission_remains_closed` (additional cases in this file).
- `tests/tui_gateway/test_bot_live_owner_delivery.py`

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Wall-clock jumps must not renew or prematurely expire an already-observed local claim. Poller-stop release is not interchangeable with expiry: cursor-based stop cleanup could otherwise rewind a newer claim. The clock lock performs no SQLite operations. These source regressions do not prove live notice delivery for a running chat.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
