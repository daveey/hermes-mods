# Notifications wait with their owner; one sidebar recompute serves every queued reader

Patches:

- `patches/0541-fix-tui-owner-addressed-notifications-wait-with-thei.patch`
- `patches/0582-perf-tui-kanban-poll-reads-only-this-chat-s-notify-s.patch`: each chat's kanban poll reads only its own subscriptions.

A Desktop backend runs one notification poller thread per open chat, and every poller drains one process-wide
completion queue. Before this patch, an event addressed to a chat whose turn was still running went back on that
queue. Every other poller then dequeued it, resolved its compression lineage against `state.db` and requeued it,
about 10 times a second each, for the whole turn. On a 22-chat backend with one long autonomous turn, that loop
pinned the backend at 300% CPU, stalled its event loop 5–13 s at a time and timed out every Desktop request
(2026-10-27). The sidebar's session list made it worse: every queued `/api/profiles/sessions` request
recomputed kanban activity in turn behind one lock, with about 24 threadpool workers stuck there.

## Behavior

- A poller that dequeues an event owned by another live chat hands it straight to that chat's poller (its held
  list) instead of the shared queue. No requeue back-off, and no other poller sees the event again.
- A chat that is mid-turn (or stopped by the user) holds its own addressed events in that list. Its poller
  retries them once the chat is idle and not stopped, so the event is delivered by its owner exactly as before.
- When a poller shuts down (chat closed or finalized), its held events are drained back to the shared queue, and
  a handoff that arrives after that drain falls back to the shared queue.
- Events with no owner (no `session_key` or `origin_ui_session_id`), and owners with no running poller, keep the
  old route: the shared queue, so any idle poller can take them.
- `kanban_session_activity._all_counts` is single flight: one caller recomputes, and every caller that queued
  behind it takes that result, even if a kanban write moved the fingerprint meanwhile. A fresh read still
  recomputes once the cache expires or the fingerprint changes.

- Each chat's poller reads only its own `platform="tui"` subscriptions on each board (filtered in SQL), not every
  subscription row on the board. `list_notify_subs` accepts `platform` (case-insensitive) and `chat_id` (exact),
  like `count_notify_subs`. Before 0582, ~20 pollers each fetched all 2,465 rows every 5 s. sqlite's `fetchall`
  re-takes the GIL per row, so ~10 threads were always queued in `take_gil` and the backend ran at ~240% CPU,
  ~210% of it system time.

## How it works

`_notification_event_belongs_elsewhere` now wraps `_notification_event_owner_elsewhere`, which returns the owning
session dict (the same branches: live UI tab, live compression continuation, another profile's lineage, a live
session matching the key). `_notif_handle_event` passes that owner to `_notif_hold_for(owner, evt, queue)`, which
appends the event to `owner["_notif_held"]` under `_notif_held_lock`, or returns False when the owner has no list.
The busy branch of `_notif_handle_event` and of `_notif_dispatch_completions` (the batch path that process
completions take) holds owner-addressed events in the session's own list. The poller loop creates the list when it
starts, retries it before each `queue.get` while the chat is idle, and removes it under the lock at shutdown,
feeding its contents into the existing deferred drain.

`_all_counts` reads `_counts_generation` on a cache miss, then takes `_counts_flight`. If the generation moved
while it waited and the scope matches, it returns the cached counts; otherwise it calls `_recompute_all_counts`,
the old body, which bumps the generation when it publishes.

## Code

- `tui_gateway/session_notifications.py`: `_notification_event_owner_elsewhere`, `_notif_live_session_matching`,
  `_notif_other_profile_session_owner`, `_notif_hold_for`, `_notif_take_held`, `_notif_held_lock`, the busy branches
  of `_notif_handle_event` and `_notif_dispatch_completions`, and the poller loop's held retry and shutdown drain.
- `hermes_cli/kanban_session_activity.py`: `_all_counts`, `_recompute_all_counts`, `_counts_flight`,
  `_counts_generation`.

## Tests

- `tests/tui_gateway/test_notification_held_handoff.py`
  - `test_foreign_event_goes_to_the_owners_poller_not_the_shared_queue`: handoff to the owner's poller, no back-off.
  - `test_foreign_event_whose_owner_has_no_poller_still_uses_the_shared_queue`: fallback route.
  - `test_a_busy_owner_holds_its_own_event[completion|async_delegation]`: both busy paths (batch and single).
  - `test_a_busy_session_requeues_an_ownerless_event_for_any_idle_poller`: ownerless events keep the shared queue.
  - `test_held_event_is_delivered_once_the_owner_goes_idle`: the poller loop delivers a held event.
  - `test_shutdown_drain_returns_held_events_to_the_shared_queue`: drain, plus the late-handoff fallback.
  - `test_busy_owner_among_many_pollers_does_not_bounce_its_event`: 7 real poller threads and a busy owner, with
    at most 3 lineage lookups in 1.5 s (96 before this patch), then delivered by the owner once idle.
- `tests/tui_gateway/test_kanban_notify_poller.py::TestPollerReadsOnlyItsOwnSubscriptions`
  - `test_list_notify_subs_filters_platform_and_chat_in_sql`: SQL filters, case-insensitive platform.
  - `test_poller_fetches_only_this_sessions_rows_and_still_delivers`: 41 subscriptions on the board, the poll
    fetches 1 row and still delivers its notice (41 rows before 0582).
- `tests/hermes_cli/test_kanban_session_activity.py::test_concurrent_sidebar_reads_share_one_recompute_while_the_board_keeps_changing`:
  8 concurrent readers with a fingerprint that changes on every read, at most 2 recomputes (8 before).
- The existing routing tests (`test_tui_gateway_server.py`, `test_launch_db_home_override_race.py`,
  `test_readopted_completion_parking.py`, `test_notification_turn_release.py`) pass unchanged.

Untested: the HTTP route `/api/profiles/sessions` end to end under concurrent load. The single-flight test calls
`_all_counts` directly.

## Apply

`git am core/patches/0541-*.patch core/patches/0582-*.patch`. Takes effect when the Desktop backend restarts.

## Gotchas

- `tui_gateway/session_notifications.py` bodies are rebound onto `server.py`'s globals, but module-level statements
  run at import in the module itself, so `_notif_held_lock = threading.Lock()` needs the module's own
  `import threading`.
- Tests that monkeypatch `server._notif_resolve_event_key` count lineage lookups. That is the cost the old bounce
  multiplied.
