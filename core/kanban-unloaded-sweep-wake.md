# Unclaimed kanban notices start a turn (sweeper wake) + poller-miss log

Patch **0518** (source `7c5b978a33`). Builds on [kanban-unloaded-session-delivery.md](kanban-unloaded-session-delivery.md) (0185/0186).

## Behavior

When no live poller claims a finished/blocked card's events within the 30 s grace, the unloaded-chat sweeper used to
write a `kanban_notice` transcript row and stop. A row is not a turn, so the chat sat green until David opened it. On
2026-10-27, 19 chats were stuck this way. In 5 of them a backend held the chat but its poller never claimed the event.

Now the sweeper first runs ONE agent turn with the notice. If that turn can't start, it writes the old passive row.

| Case | What happens |
|---|---|
| Normal | `server._wake_session_turn(tip, text, display_kind="kanban_notice")` runs on a worker thread. If no backend holds the chat, it is resumed in this process. Refused if an attached backend owns it; a detached owner's lease is taken over. |
| Wake returns busy / refused / missing / error, or raises | Passive row, written by the worker. |
| Card is `blocked`/`triage` with a validated human request | Passive row (`skipped:human`). The chat is already red, and the ask is David's. |
| Diagnostic-only notice | Passive row (`skipped:diagnostic`). |
| Chat already had `HERMES_KANBAN_UNLOADED_WAKES_PER_HOUR` started wakes in this process (default 4) | Passive row (`skipped:budget`). Only started turns spend budget. |
| Both wake slots busy (`MAX_PARALLEL_WAKES` = 2) | Passive row now (`skipped:slots`). |
| `HERMES_KANBAN_UNLOADED_WAKE=0` | Passive row (`skipped:disabled`). |

**Poller-miss log.** Another live backend in the active-session registry may hold the chat. Then reaching the sweeper
means that backend's live poller (which claims within about 5 s) missed the event. The sweeper logs
`kanban poller miss: chat … is held by pid N (surface@host) …` at WARNING, so the miss can be root-caused. Every
delivery logs its verdict (`wake=started|dispatched|skipped:…|refused|…`).

**Guarantee.** Claimed events always end as a turn or a row. Any error after the claim falls back to the row, and a
failed row rewinds the claim.

## How it works

All in `tui_gateway/kanban_unloaded.py`:

- `_sweep_sub` claims the events while it holds the store. It builds the notice and metadata, and the metadata keeps
  `notice_id` for row dedupe. It then releases the store before any wake: an agent build can take up to 120 s.
- `_route_notice` makes the decision and never raises:
  - It looks up the holder, using `registry_holders()` via `active_session_registry_snapshot()`, and logs a poller
    miss when another process holds the chat.
  - It applies the skip rules.
  - It checks the budget.
  - It takes a non-blocking slot from `_wake_slots`.
- `_wake_job` runs on `spawn_context_thread`, which keeps the profile scope. It calls `wake`. On `started` it charges
  the budget. Otherwise it writes the row with `_persist_notice` on a fresh board connection, rewinding on failure.
  Finally it releases the slot.
- `sweep_once(..., wake=wake_turn, holders=registry_holders, spawn=_spawn_thread)` makes these seams injectable.

## Tests

`tests/tui_gateway/test_kanban_unloaded_wake.py`:

- **Turn instead of row:** a finished card starts a turn, not a row, and is claimed once.
- **Fallback:** each non-started verdict (busy, refused, missing, error) and a raising wake fall back to the row.
- **Skips:** a human-blocked card and a diagnostic-only notice are not woken; disabling the wake falls back to the row.
- **Budget:** it stops create→notice→wake loops, and failed wakes don't spend it.
- **Poller miss:** a chat held by another backend logs the miss with its pid; an unheld chat logs nothing.
- **Never lost:** an error after the claim still delivers the row.
- **Threading:** the wake runs off the sweeper thread, and a failed one writes the row there; with no free slot the
  row is written immediately.

`tests/tui_gateway/test_kanban_unloaded_sweep.py` keeps the passive-row contract. It now runs with
`HERMES_KANBAN_UNLOADED_WAKE=0` (autouse fixture) instead of passing through a real resume.

## Applying

`git am -3` 0518 after 0185. It needs `server._wake_session_turn`, from the chat-closeout wake primitive, and
`agent.memory_provider.spawn_context_thread`.

## Gotchas

- **The budget is per process.** Every backend runs a sweeper, so the real cap is processes × N per hour.
- **After the first wake the chat is live in that process.** Later notices go through its live poller with no budget,
  the same as any open chat.
- **A refused or errored wake can leave the chat resumed in the sweeper's process.** Future sweeps there skip it, and
  that process's own poller delivers instead.
- **Residual duplicate:** if `_notif_submit` raises after the turn thread already started, `_wake_session_turn` says
  `error` and the row is written too.
- **The human-blocked check reads the task before the claim**, so a status that changed in that instant can be stale.
