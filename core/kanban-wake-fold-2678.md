# Kanban wakes queued behind a busy turn fold into one turn

Patches: **2597–2600, 2655, 2726**.

## Behavior

- Kanban wakes (producer `kanban-wake`) for a chat whose turn is still running used to each commit their own FIFO row, so a chat with long turns fell further behind with every card event, and each late wake could describe a card state that no longer held (e.g. "ready with no worker" for a card done hours earlier).
- Now a wake admitted while another Kanban wake for the same route is the session's **last** queued input joins that row: the waiting row is cancelled and one row replaces it, built from the cards of both. Several events for one card collapse to the latest. With more than one card the text starts with `[IMPORTANT: N Kanban task updates arrived while this session was busy. ...]`.
- A card whose premise no longer holds when the batch is built is relabelled with its live status (`Note: an earlier wake for this card said "..."; that no longer holds. Live status: ...`), with a one-line `Result:` added for a done card. Premises covered: unclaimed wait (card now claimed, done, etc.), block / `block_loop_detected` (card no longer blocked or in triage), `crashed` / `timed_out`. Outcome kinds such as `completed` or `review_requested` are never relabelled; a `completed` wake for a done card stays as delivered. An unreadable board leaves the card as delivered. `failure_finalized` is rendered via `describe_failure` and has no label.
- Every card keeps its `[kanban] Task <id> <kind>.` line, which the follow-through and ask-gate plugins match.
- Only the tail folds. A wake never jumps ahead of, or folds across, a prompt or a process-completion row queued after it; a completion never folds into a wake; a row already claimed by the drain is never rewritten (the new wake queues alone, and later wakes fold among themselves).
- A wake queued before the upgrade (no stored cards) still folds: it is parsed from its text into one card.
- A fold the store refuses (more than 200 distinct cards, `invalid_params`) queues the new wake alone instead of raising. Before 2655 that raised from `deliver_wake` and refused every later wake to the chat until the drain claimed the tail.
- The batch mutes only when every wake in it was a muted category.

## How it works

- 2597: new `gateway/kanban_wake_fold.py`. The notifier (`gateway/kanban_watchers_notifier.py`, `_KanbanNotification.wake_card`) and the TUI-owner delivery (`gateway/kanban_tui_owner.py`) attach `kanban_cards` (`{task_id, board, kinds, text}`) to the wake; `gateway/wake.py` `deliver_wake` takes a `kanban_card` argument (text capped at 32000 chars) and passes it as event metadata. `gateway/session_automation.py` accepts `kanban_cards` as automation metadata (validated by `card_metadata`, max 200 cards) and stores it in the row descriptor. `admit_route_wake` goes through the new `_admit_route_wake_event`: under a per-runner asyncio lock it reads the tail (`waiting_wake`), rebuilds the text with `fold_into` off the event loop, and admits with `replaces=<waiting row>`; a `stale_generation` result (the row was claimed first, or a prompt landed behind it) falls back to the unfolded wake. `_fold_queued` also drops `kanban_cards` from the cancelled row. New locale keys `gateway.kanban.wake.dependency_parent_unclaimed` and `chat_wait_unclaimed` (all locale files).
- 2598: developer-guide docs, and a test that every wake kind has a status label.
- 2599: tightens the tests so each fails on the parent commit (prompt order, claim guard and completion guard also fold).
- 2600: test fix: `failure_finalized` renders via `describe_failure`, no label expected.
- 2655: `_admit_route_wake_event` also falls back to the unfolded wake on `invalid_params`; `_live_task` reads under `kb.pin_first_board_resolution()` (as the notifier does), so on a box that pins `HERMES_KANBAN_DB` the relabel reads that DB instead of creating a per-slug board DB.

## Code

`gateway/kanban_wake_fold.py` (new), `gateway/kanban_tui_owner.py`, `gateway/kanban_watchers_notifier.py`, `gateway/session_automation.py`, `gateway/wake.py`, `locales/*.yaml` (17 files), `website/docs/developer-guide/gateway-internals.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_wake_fold.py`, `tests/hermes_cli/test_kanban_notify.py` (2597: the notifier's wake call carries `kanban_card`). Entry path: `gateway.wake.deliver_wake` with a `kanban_card` on a local session and on a Discord-style adapter session, reading the FIFO via `list_session_admissions` (five wakes fold into one turn; a prompt queued after a wake is never jumped, including with a stale tail read; a claimed row is untouched; relabel of a completed unclaimed wait; one line per card; process-completion folding unchanged and never crossing a wake; pre-upgrade row folds; over-cap wake queues alone; relabel reads the env-pinned DB; `card_metadata` rejects malformed cards; every wake kind has a status label). The real notifier-to-gateway path end to end, other platform adapters, the locale strings' rendering, and the TUI-owner `kanban_cards` metadata are **untested**; the docs change is not tested.

## Follow-up 2726: a queued wake is relabelled when the drain claims it

- Behavior: a wake that nothing folded into used to run with the text it was built with, however long it waited (a card claimed minutes earlier could still be announced as having no worker). When the drain claims a `kanban-wake` row, its text is now rebuilt from the row's stored cards and their live state, with the same relabel and compose path the fold uses; every card keeps its `[kanban] Task <id> <kind>.` line and the trailing guidance. Only the in-memory claim payload changes, never the stored row. An outcome-only batch makes no board read. An unreadable board (any card), another producer's row, a row without stored cards, or text that no longer has the shape its cards built leaves the text unchanged.
- How it works: `kanban_wake_fold.refresh_claimed(payload)` validates the descriptor and producer, extracts the guidance suffix with `_guidance` (matching each card's head line in order), and recomposes with `_compose(cards, guidance, strict=True)`; `refresh_card` gains `strict` so an unreadable card raises instead of undoing the relabel. `fold_into` now uses the same `_compose`. `session_ingress.execute_admission` calls it, then the block-ask refresh, in one off-loop thread.
- Code: `gateway/kanban_wake_fold.py`, `gateway/session_ingress.py`.
- Tests: source coverage, not run in this mirror: `tests/gateway/test_kanban_wake_fold.py`. Entry path: `execute_admission` on a claimed row against a temporary board (lone stale unclaimed wait relabelled; a still-true premise byte-identical; a folded row relabelled again; unreadable board unchanged) and `refresh_claimed` directly (unparsable and foreign rows untouched). A real gateway drain with a live notifier and other platform adapters are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The fold lock is per process (`wake_lock`); a second gateway process admitting to the same session is only guarded by the store's write guard, which makes the loser queue alone.
- Folded rows keep each card's stored text as delivered; the live relabel is redone each time the batch is rebuilt, and (2726) again at claim time for every wake row, folded or not.
- Folding applies only when the waiting row has no `display_kind`.
- Related: [kanban-stale-block-ask-refresh-2678.md](kanban-stale-block-ask-refresh-2678.md) refreshes a block notice's ask when its row is executed.

## Plugin

Core change; no plugin required.
