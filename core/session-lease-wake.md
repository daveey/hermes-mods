# Turns survive laptop sleep, and a lease-lost turn is visible

Patches:

- `patches/0086-fix-agent-renew-a-lapsed-turn-lease-by-compare-and-s.patch`: agent. A turn whose session lease
  lapsed while the process wasn't running (laptop sleep) renews the lease instead of being killed, as long as nobody
  else claimed the session.
- `patches/0087-fix-tui-show-a-lease-lost-turn-as-a-visible-retryabl.patch`: backend. A turn that really lost its
  lease ends as a visible, retryable failure instead of a silent cancel.
- `patches/0088-feat-desktop-dedicated-error-card-for-a-lease-lost-t.patch`: desktop. That failure gets its own
  error card ("Hermes stopped this reply") with Retry.

What you see: close the lid while agents are working, open it 5+ minutes later, and they keep going. Stock Hermes
hard-interrupted every running turn on wake. Desktop showed nothing, so the chat just looked stopped. If a turn
really loses its session to another Hermes process, the transcript now shows an error card that says so, and Retry
runs the turn again.

## Behavior

- **Sleep longer than the 300 s lease TTL no longer kills turns.** The first refresh tick after wake renews the lease
  when the row still names this turn as holder. The log line is `Session turn lease renewed Ns after it lapsed
  (process suspended or starved; no other owner claimed the session)`.
- **A real takeover still stops the turn.** If another process reclaimed the expired row, or claimed and released it,
  during the lapse, the renewal misses. The turn stops with `Lost session turn lease while turn is active (another
  owner claimed it during a Ns lapse)`.
- **A locked state.db on a lapsed row** (many sessions renewing at once on wake) is a missed tick, retried at the next
  interval (60 s).
- **Lock contention while the lease is still valid** is unchanged from upstream: the turn stops before the row
  expires, logged as "stayed locked through its lifetime".
- **A lease-lost turn in Desktop** gets an error card titled "Hermes stopped this reply". The body says another
  Hermes process took over the chat, the work done so far is kept, and to retry. The card has a Retry button. Any
  streamed text stays as a partial reply, and the turn's inflight snapshot is retained, so a reconnect still shows
  it. The TUI and other clients get the text "Hermes stopped this turn because it lost ownership of the session.
  Your message was not answered." plus the detail and "Send /retry to run it again."
- A human stop (Stop button, new message) is unchanged: it stays a silent cancel.

## How it works

- Root cause: `agent/periodic_scheduler.py` times ticks with the monotonic clock, which stops during macOS sleep.
  The lease row's `expires_at` is wall clock and keeps running. On wake the first tick found no time left and called
  `_stop_on_exhausted_authority` without trying to renew. On the author's MacBook, all 36 "stayed locked through its
  lifetime" events over two days lined up with a clamshell or maintenance sleep longer than 300 s in `pmset -g log`.
  None had a lock-retry warning before it.
- 0086: `DurableTurnLease.refresh_tick` sends a tick with no patience left to `_renew_after_lapse`. That calls
  `refresh_session_turn_lease`, a holder-qualified `UPDATE` that runs inside BEGIN IMMEDIATE and so acts as a
  compare-and-swap against acquisition. Expiry only makes a row reclaimable; expired rows are swept after 24 h. The
  transcript-write fence in `hermes_state_messages.py` already renewed a lapsed row that still named its holder, and
  the refresher now follows the same rule.
- 0087: `agent/turn_finalizer.py` stamps `result["interrupt_issuer"]` (the slug from
  `interrupt_control.interrupt_issuer`, absent for human stops). `tui_gateway/prompt_turn._surface_system_interrupt`
  rewrites an `interrupted` result whose issuer is `session_turn_lease_lost` into `failed` with
  `failure_reason=session_turn_lease_lost`, `failure_retryable=True`, and `partial` when it has text.
  `agent/error_surface.py` maps the reason to the gateway layer, and `tui_gateway/user_messages.py` has the copy.
- 0088: `session_turn_lease_lost` added to `ERROR_CODE_KEYS` with en/de/es/fr copy. ar/ja/ru/zh/zh-hant are listed
  in `overlay-gaps.json`.

## Tests

- `tests/agent/test_turn_facade_lease.py`: a real SessionDB with a moved wall clock. A 418 s stall with no other
  owner keeps the turn and renews the row. Controls: a successor that reclaimed the row, and a successor that came
  and went, both stop the turn. A locked renewal on a lapsed row is retried.
- `tests/agent/test_run_agent.py::...::test_lease_lost_interrupt_stamps_its_system_issuer`
- `tests/tui_gateway/test_lease_lost_turn_visible.py`: error payload, error_surface, retained inflight, partial
  text, and a control showing a human stop is unchanged.
- `apps/desktop/src/lib/error-surface-copy.test.ts`: the dedicated card, with Retry.

## Applying

Apply 0086 alone for the sleep fix. 0087 and 0088 go together: without 0088, the failure shows the generic "Hermes
hit a problem" gateway card. After applying 0088, run `npm run i18n:keys`.

## Gotchas

- There is no auto-resume. After 0086, the remaining lease-lost cases are real takeovers by another process, where
  re-running automatically would fight the new owner. Retry is a click.
- While the turn is asleep or starved, its tool side effects are not fenced. Only its transcript writes are.
  Upstream has the same gap.

## Plugin?

No. The refresher and the turn-result shaping have no plugin hooks.
