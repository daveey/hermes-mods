# A cron result refused by a busy Bot Chat is queued, not lost

Patch:

- `patches/0136-fix-cron-defer-Bot-Chat-delivery-refused-as-busy-ins.patch`: backend
  (`cron/scheduler_delivery.py`, `cron/bot_chat_delivery.py`).

What you see: two `deliver: bot-chat` cron jobs of one profile can now fire in the same minute. The second result
lands in Bot Chat right after the first turn finishes. Before, it failed with "Hermes could not deliver this result
to Bot Chat ... hermes-refusal-reason: SESSION_NOT_OWNED" and was never posted.

## Behavior

- When the Bot Chat CLI delivery child (`hermes chat -c "Bot Chat" -Q`) is refused at admission with
  `SESSION_NOT_OWNED` (another delivery or CLI session holds the chat's lease) or `MAX_CONCURRENT_SESSIONS`, the
  payload is deferred with `cron.bot_chat_delivery.defer` under the run's delivery key. The job records a `queued`
  receipt ("completion unverified; do not resend"), not a delivery error.
- The deferred lane (drained every scheduler tick) posts it once the session frees. A replay refused the same way
  goes back to `queued` and counts `busy_attempts`. After `MAX_BUSY_ATTEMPTS` (120 drains, about 2 h) it settles
  `ambiguous` with the refusal as its error, so a stuck lease can't spawn refused children forever.
- It is posted exactly once: the CLI refuses before reading the query, so the refused turn never ran, and a repeat
  of the same execution finds the settled record.
- Other nonzero exits, `SESSION_COORDINATION_UNAVAILABLE` included, stay final failures as before.

## How it works

- Root cause (nous-1 coach, 2026-10-18 15:00 UTC: `c2dda3003f22` at 15:00:23, `e16b03040ddf` at 15:00:29):
  `_deliver_to_bot_chat` already defers when discovery sees a canonical non-live owner. But the first delivery
  child hadn't claimed its lease yet when the second delivery ran discovery (CLI startup is slower than 6 s), so
  both took the CLI lane. The second child got `SESSION_NOT_OWNED` and the nonzero exit was booked as final.
- `_bot_chat_refusal_reason` parses the `hermes-refusal-reason:` line (`active_sessions.format_refusal_stderr`).
  For the busy codes, the CLI lane calls `defer(...)` (first attempt) or sets `job["_bot_chat_busy_requeue"]`
  (a replay); `_drain` sees the flag and writes the record back as `queued`.
- Upstream main has the same loss. Issue draft: `docs/upstream-drafts/cron-bot-chat-session-not-owned.md` in the
  private state repo, ready to file.

## Tests

- `tests/cron/test_bot_chat_busy_defer.py`: for both busy codes, the second delivery ends `queued`, stays queued
  through a drain refused again, settles after release and is posted exactly once (also when the same execution
  is delivered again); a `SESSION_COORDINATION_UNAVAILABLE` refusal stays a final failure with no deferred record;
  the requeue cap settles `ambiguous`. The first and third fail on the old code.
- Real-CLI repro (scratch script, not in the repo): temp `HERMES_HOME` with a `Bot Chat` session, lease claimed
  after discovery and before the real `hermes chat -Q` child. Old code printed the production error. New code:
  `queued`, a drain while the lease is held spawns no child, and after release it is `settled` with 1 post.
- Existing `test_bot_chat_pending.py`, `test_bot_chat_timeout_marker.py`, `test_cron_bot_chat_delivery.py` and the
  other `test_bot_chat_*` files pass unchanged.

## Applying

Core-only; no config. Takes effect when the gateway / cron ticker restarts on the new core.

## Gotchas

- nous-1 runs a Nous-managed core without this commit. Its coach scripts keep the workaround
  (`coach_common.wait_for_bot_chat` + ≥45 min between bot-chat schedules) until that core carries the fix.

## Plugin?

No: the refusal is only visible inside `_deliver_to_bot_chat`'s CLI lane and the drain loop.
