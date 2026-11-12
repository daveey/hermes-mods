# Headless wake turns release their writer lease and are reaped after a grace

Patches: **2577**.

## Behavior

- A headless gateway (a messaging gateway or sweeper process with no attached RPC client) that wakes an unloaded chat for a notice used to keep that chat live, and its active-session writer lease held, indefinitely. The lease blocked a Desktop-facing backend from taking the chat's writer slot. Now the session is bounded after each wake turn settles.
- When a headless wake turn settles, the session gets a grace timer (default 60 s, set by `HERMES_TUI_DETACHED_TURN_GRACE_S`). If nothing else touches the session before it fires, the session is popped and closed with end reason `ws_orphan_reap`, and the active-session registry entry and lease are released (another process can claim the same session key afterwards).
- Notices that arrive inside the grace reuse the live session and its poller; each newly settled idle turn restarts the grace. After cleanup, later notices go back to the unloaded-chat sweeper path.
- A session with live background work is not closed: a running process, active delegations, a live wakeup schedule, a pending process notification or a pending request. It keeps its pollers but still releases the idle writer lease. The timer re-arms, so once the work finishes the session is reaped on a later fire.
- An old timer cannot close a session whose state changed: it is a no-op if the session is running again, was re-attached to a real transport, or was already removed. A timer firing early re-arms for the remaining grace instead of closing.
- Unchanged: a standalone `hermes --tui` stdio session (which has an RPC client) keeps its lease and gets no timer, and a websocket disconnect keeps its own reconnect/interrupt timer policy (no shortened grace).
- The reaper's dead-transport test now treats the shared stdio transport as dead when it is not an RPC channel (headless); it is still live for an RPC stdio session.

## How it works

- New `tui_gateway/detached_turn_cleanup.py`. `settled(session)` runs under the session's `history_lock` then `_sessions_lock` (the same order as resume, never waiting for history while holding the registry lock), revalidates that the record is still the live one, idle and headless, releases the writer lease if the session is not reapable, cancels any old timer and arms a new `Timer(GRACE_S)`. The timer body takes the resume lock, `history_lock` and `_sessions_lock`, re-checks ownership and idle state, honors the remaining grace, then pops the session and runs `_teardown_popped_session(..., end_reason="ws_orphan_reap")` outside the locks. Exceptions are logged and the timer slot is cleared so `repair()` can retry.
- `repair()` (called from `_reap_idle_sessions` in `session_reaper.py`) arms a timer for any headless session that lacks one, without resetting an existing grace; this is the safety net for failed dispatches and lost timers. `_repair_missing_ws_orphan_reaps` skips the stdio transport so the websocket path does not touch these sessions.
- `settled` is called from every place a headless wake turn can end: `turn_admission.settle_prompt_state`, `session_notifications._notif_release_turn` (a rejected notice), and `compute_host_bridge._on_compute_host_turn_done`. `cancel` is called on `_teardown_popped_session` and `_rebind_live_transport` so a real attach or close removes the timer.
- `session_reaper._transport_is_dead` returns `not _stdio_is_rpc_channel` for the stdio transport. Docstring in `kanban_unloaded.py` updated to describe the grace.

## Code

`tui_gateway/detached_turn_cleanup.py` (new), `tui_gateway/session_reaper.py`, `tui_gateway/turn_admission.py`, `tui_gateway/session_notifications.py`, `tui_gateway/session_lifecycle.py`, `tui_gateway/compute_host_bridge.py`, `tui_gateway/kanban_unloaded.py` (docstring only).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_headless_wake_reap.py`. Entry path: `server._wake_session_turn` over a stubbed session with a fake `Timer` and clock, driving `turn_admission.settle_prompt_state`, `_notif_release_turn` and the timer callback, plus `detached_turn_cleanup.settled` and `repair` directly. Covers: grace reap with the registry snapshot empty and a second process able to claim the key; RPC stdio keeping its lease; each live-work guard keeping the session but releasing the lease; two notices reusing one session; old-timer no-ops after running/re-attach/removal; early fire re-arming; later reap once background work finishes; websocket policy unchanged; `repair` arming a missing timer without resetting the grace; a real notice turn through to teardown; lock order against `history_lock`.

**Untested**: the call from `_reap_idle_sessions` to `repair()`, the `compute_host_bridge` settle hook, the `cancel()` calls in `_teardown_popped_session` and `_rebind_live_transport`, the `_repair_missing_ws_orphan_reaps` stdio exclusion, and a real attached client.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The grace is a daemon `threading.Timer`; a process that exits drops it (nothing to reap then).
- A chat with long-running background work keeps its poller and its in-process session until that work ends plus one grace.
- Notices arriving in the gap between the reap and the next sweeper pass take the unloaded-chat path (a passive notice row or a fresh cold resume).

## Plugin

Core change; no plugin required.
