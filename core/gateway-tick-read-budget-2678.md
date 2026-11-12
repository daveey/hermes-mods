# Gateway tick: read once per pass, not once per card

Patches: **2664, 2665**.

## Behavior

- A hub gateway tick no longer repeats the same read for every card. Perf-only; no pass, notice or retry is removed and no output changes.
- Divergence repair reads the replica once per 500 settled cards and the omissions table once per pass, instead of one `SELECT` of each per synced card every sync round.
- A tui chat's owner claim asks `owned_live_session` once per chat per collector tick; all of that chat's pending rows share the answer. The delivery re-check still reads fresh.
- A wait notice (and the parent-unclaimed notice) that is already posted for a wait no longer computes the queue place on later ticks; the cheap already-posted check runs first.
- `gateway_names()` reuses its answer for at most 5 s while `hub.db`, its WAL and the mail config keep identity, size and mtime; a file written within the last 100 ms is never kept.
- 2665 refines the divergence repair of 2664: only one replica chunk is held in memory at a time, rather than the replica rows of every settled card in the pass.

## How it works

- **2664** `hermes_cli/kanban_sync_divergence.py`: `repair_divergence` filters held and out-of-sync cards up front, loads omissions once into a dict keyed by row uid, and fetches replica rows with `WHERE id IN (...)` in chunks of `_CHUNK = 500`. `gateway/kanban_tui_owner.py`: `_owned_this_tick` caches `owned_live_session` on the collector, keyed by (notifier profile, chat id, has-thread). `hermes_cli/kanban_parent_unclaimed.py`: `_already_posted` runs before `_place` in `post_notice`; the in-transaction check is kept, so concurrent posters still cannot double-post. `hermes_cli/kanban_origin.py`: `gateway_names` memoises on a key of stat signatures (inode, mtime, size) of hub.db, hub.db-wal and the mail config, with `_NAMES_MAX_AGE_S = 5.0` and a racy-timestamp guard (`_NAMES_RACY_NS`); the uncached read moved to `_read_gateway_names`.
- **2665** same file: the replica dict is now replaced at each chunk boundary (`index % _CHUNK == 0`) instead of built for the whole pass first; the omissions read stays once per pass. The test gains a parametrised chunk size so a card in a later, partial chunk is covered.

## Code

`gateway/kanban_tui_owner.py`, `hermes_cli/kanban_origin.py`, `hermes_cli/kanban_parent_unclaimed.py`, `hermes_cli/kanban_sync_divergence.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_gateway_tick_read_budget.py` (2664, 2665), `tests/gateway/test_kanban_tui_owner_delivery.py` (2664). Entry path: `ksc.sync_board` with a fake service (replica read count, including chunk sizes 500 and 3 with 7 cards, and that an uncaptured local edit is still healed); the tui owner claim path (one lineage read per tick regardless of card count); `post_notice` for wait and parent-unclaimed notices (no queue place once posted); `gateway_names` (one hub.db open per pass, an immediate hub.db write is seen, re-read after the age bound, a just-written file never kept). The tests assert operation counts, not CPU savings; the percentages in the source message are from a hub profile. The in-transaction duplicate check in `post_notice` under concurrency, and a mail-config-only change to `gateway_names`, are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- `gateway_names()` can serve a result up to 5 s old if hub.db, its WAL and the mail config all keep the same inode, size and mtime; a file modified within the last 100 ms is never memoised.
- The per-tick owner answer is stored on the collector object, so a chat that gains or loses an owned live session mid-tick sees the change on the next tick.

## Plugin

Core change; no plugin required.
