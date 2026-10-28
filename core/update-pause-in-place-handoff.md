# A core-update-paused turn waits for a successor backend only while one is coming

Patch: [`patches/0704-fix-core-update-a-paused-turn-continues-in-place-onc.patch`](patches/0704-fix-core-update-a-paused-turn-continues-in-place-onc.patch)
(fork commit `e96d22b072`, card t_01a18300e42c3364)

## Why

A drain window pauses auto-continuable Desktop turns at a step boundary (`tui_gateway/update_pause.py`), and
the update then applies. If the update moves the checkout, the paused turn's own backend leaves it to the
next backend, which continues it from its startup sweep. But that backend's code-skew watchdog
(`hermes_cli/web_server_skew_exit.py`) retires it only through the retirement fence's idle proof. A backend
kept busy by other chats never becomes idle, and a backend with no watchdog is never retired at all. The
in-place watcher still waited its full `LEAVE_GRACE_S` (900 s) before continuing the turn itself.

On David-MBP on 2026-10-28, three paused turns on serve pid 74105 logged "checkout moved" at 07:03:53. That
serve outlived the 07:02:50 update. The turns logged "backend still not replaced after 900s" at 07:18:55, and
session bef6c6 resumed at 07:19:28, a 19.5 min stall.

## Behavior

Once the checkout has moved, the watcher (`start_in_place_watcher`) checks `replacement_outlook()` on every
poll. The poll interval is 5 s.

- **`coming`**: a successor is on its way, so the turn is left to it. This means the retirement fence is
  preparing or holds a permit (`RetirementFence.retiring()`), or the skew watchdog is running
  (`skew_watchdog_armed()`) and `idle_proof()` reports idle.
- **`none`**: no skew watchdog runs in this process. The turn continues here on the next poll.
- **`blocked`**: the watchdog runs, but other work, a pending prompt, an unreadable ledger or a live update
  marker keeps the backend up. After `NO_REPLACEMENT_S` (60 s) of this with no `coming` in between, the turn
  continues here.
- **Backstop**: after `LEAVE_GRACE_S` (now 300 s), the turn continues here anyway.

The window-ended-without-an-update path is unchanged. The skew check still sends the turn to `leave` first,
and a backend that is retiring never continues the turn itself.

A turn that has already moved on reports `done` before the skew check, so it is never handed off or resumed
again. "Moved on" means it is running, already scheduled (`_auto_continue_scheduled`), closing or finalized,
or its marker is no longer a pause.

The in-place continuation and the successor's continuation are exclusive. The in-place continuation is
admitted through the retirement fence (`turn_admission.start_work`), so it is refused once retirement prepares
or commits, and the paused marker then stays for the successor's startup sweep. While the in-place continuation
runs, the session is busy, so the idle proof refuses retirement.

## Tests

`tests/tui_gateway/test_update_pause_recovery.py` runs the real watcher thread on a virtual clock:

- `test_no_replacement_coming_continues_in_place_within_the_bound`: the checkout moved and the backend is kept
  busy, and the turn continues within 70 s. It fails on the old code at 905 s.
- `test_no_retirement_watchdog_continues_in_place_at_once`: no watchdog, and the turn continues on the first
  poll.
- `test_skew_with_a_replacement_coming_does_not_continue_in_place`: the backend is skewed, idle and has a live
  watchdog, and no in-place continuation happens.
- `test_requested_replacement_hands_off_and_the_successor_continues_once`: the fence commits retirement. No
  in-place continuation happens even past every bound, and the successor's startup sweep continues the turn
  exactly once.

`tests/hermes_cli/test_web_server_skew_exit.py::test_watchdog_reports_armed_while_polling_and_not_after_it_stops`
covers `skew_watchdog_armed()` and `RetirementFence.retiring()`. `tests/tui_gateway/test_update_pause.py::test_in_place_resume_decision`
covers the decision order.

Not covered by a test: a live drain window on David-MBP where the paused turn's serve survives the update. That
check is tracked on its own card.
