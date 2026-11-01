# A Kanban worker transcript is never a notify destination

Patch: `patches/1501-fix-kanban-never-subscribe-inherit-link-or-notify-a-.patch` (`db08b43713`, `fix(kanban): never subscribe, inherit, link or notify a Kanban worker transcript`).

Related: [Desktop opens Kanban worker sessions read-only](desktop-kanban-worker-readonly.md) (the `session.resume` 4132 refusal this patch stops triggering), [stored chat outcome](session-outcome.md) (notice fan-out), [chat-link edges in sync](kanban-sync-links-and-placement.md).

## Behavior

The incident: a hub card picked up five shepherd subscriptions belonging to another gateway's worker transcripts. They were copied onto it when it was linked under a parent (`link_tasks` → `_inherit_notify_subs`). When the card finished, that gateway's unloaded/remote sweep wrote the done notice into each transcript and tried to wake each one, and every wake was refused (`session.resume` 4132, "is a Kanban worker transcript"). The test docstring also records 69 hub `shepherd` requests in which workers subscribed their own transcripts.

A headless receipt session (a `local-<64 hex>` id, written for a Kanban worker run, cron job or one-shot), bare or as `<gateway>/<profile>/<session>`, is now never a notify target:

- **Subscribe.** `add_notify_sub` silently skips `hub`/`tui` subscriptions on such a session, as it already did for placement placeholders. A normal chat id is still subscribed.
- **Inherit.** `_owning_sub_clause` excludes `hub`/`tui` rows whose `chat_id` matches the headless glob, whatever their `reason`. Rows written before the refusal (with owning reasons such as `create` or `origin`) therefore do not spread to children through `link_tasks`, `parents=` or `creator_task_id`.
- **Chat links.** `kanban_chat_links.reconcile` does not write a `shepherd` link for a headless session, from local subs or from the ledger, and it deletes an existing one (counted in `retired`, like placeholder links). A `created` link from a worker stays: it records which run made the card and wakes nothing.
- **Remote sweep.** `_deliver_link` (`tui_gateway/kanban_remote_notices.py`) settles a worker target without writing a transcript row or waking it. The ledger row is `SETTLED`, `settled='worker_transcript'`, with a `swept` row of the same reason, so a restarted backend never offers it again. This applies with and without a waker (the passive, row-only path).
- **Unloaded local sweep.** `_deliver_sub` (`tui_gateway/kanban_unloaded.py`) treats a worker sub as skipped (`worker_transcript`), claims its events non-durably, writes no row, sends no wake and removes the sub. Chats on the same card are still woken.

For the two sweeps, a worker transcript is either a `local-<sha256>` id or, for older worker ids that look like chats, a session the store tags as Kanban-owned (`SessionDB.is_kanban_owned_session`).

## How it works

`hermes_cli/kanban_db_notify.py` adds `is_headless_session(chat_id)` (`local-[0-9a-f]{64}` matched against the last `/` segment) and a SQL `GLOB` form used inside `_owning_sub_clause`. `tui_gateway/notice_fanout.py` adds `worker_transcript_reason(db, chat_id)`. It returns `"worker_transcript"` for a headless id or a store-tagged Kanban session, and never raises (`sqlite3.Error` from the probe reads as a chat). `_settle_unread` in the remote sweep does an `INSERT OR IGNORE`, or updates an undelivered row that no one holds. It leaves an in-flight claim or a `DELIVERED` row untouched, and on no change it only remembers the memo key.

## Code

`hermes_cli/kanban_db_notify.py`, `hermes_cli/kanban_chat_links.py`, `tui_gateway/notice_fanout.py`, `tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_unloaded.py`.

## Tests

From the patch; **not run for this mirror**:

- `tests/hermes_cli/test_kanban_notify_inheritance.py`: `test_worker_transcript_subs_never_reach_a_child` (parametrized `link` / `parents` / `creator`: only the real chat sub is inherited), `test_worker_transcript_is_never_subscribed` (`hub` and `tui`, via the `notify-subscribe` CLI helper `_subscribe_cli`; a chat control is subscribed).
- `tests/hermes_cli/test_kanban_chat_links.py::test_a_worker_transcript_gets_no_shepherd_link_and_an_old_one_is_retired` (ledger and local subs; the `created` link stays; `retired == 1`).
- `tests/tui_gateway/test_kanban_worker_transcript_notices.py` (new): `test_remote_sweep_settles_worker_transcripts_without_a_row_or_a_wake` (receipt id and store-tagged older id; ledger settled; restart sweep returns nothing), `test_passive_remote_sweep_writes_no_row_into_a_worker_transcript`, `test_unloaded_local_sweep_drops_a_sub_on_a_worker_transcript`.
- Untested: tool auto-subscription in `tools/kanban_tools.py` and REST subscription paths (only the CLI helper is used); `add_notify_sub` refusing a store-tagged older worker id. The subscribe guard is the regex only: older non-`local-` worker ids are caught only by the two sweeps.

## Applying

Apply in full-series order with `git am -3 --empty=keep` (see [README.md](README.md) Applying). It touches files shared with earlier notify, chat-link and remote-notice patches. No config.

## Gotchas

- Existing rows are not bulk-deleted. Old worker subs stop spreading (inheritance) and are dropped only when the unloaded sweep reaches them. Old shepherd links are retired at the next reconcile.
- `add_notify_sub` returns without error for a refused worker session, so a caller cannot tell it was skipped.
- The headless pattern is a format contract with `hermes_state_keys.local_identity`. If that receipt format changes, these guards stop matching.

## Plugin?

No. Subscription storage, inheritance SQL, chat-link reconcile and the notice sweeps are core Kanban/TUI-gateway paths with no plugin hook.
