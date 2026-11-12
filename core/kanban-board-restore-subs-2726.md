# A Board-restored Kanban card gets its hub delivery subscriptions back

Patches: **2713, 2714**.

## Behavior

- `kanban_notify_subs` is local to one machine and does not sync, while a card's `task_chat_links` do. A machine reprovisioned or rebuilt from the Board therefore got its cards and chat links back but no `platform='hub'` subscriptions. The hub collector derives a card's published `sessions`, and the notifier its deliveries, only from those rows, so restored cards were republished with `sessions=[]` and their origin chats on other gateways stopped hearing from them.
- Now, when the sync apply inserts a card this gateway owns (`created_on` is this gateway or unset) into a primary live board file, it records it in the new local-only table `kanban_restored_cards` (`task_id`, `restored_at`). Our own cards are only ever inserted by the feed on a restore, so such an insert is a restore.
- Each new `task_chat_links` row of a recorded card then gets its hub subscription `<gateway>/<profile>/<session>` back when all hold: the link is not released; its gateway is not this one; gateway, profile and session are plain names (`[A-Za-z0-9_.-]{1,128}`; a NULL or empty profile is never widened to `default`); the link existed at the restore (`created_at` not later than `restored_at` + 300 s clock skew); the card is not archived; the session is not a placeholder or headless one.
- The subscription is caught up (`last_event_id` is the card's newest event, no history replay) with metadata reason `origin` for a `created` link and `shepherd` for a `shepherd` link, plus `restored: board`. Two links for one chat give one subscription; an `origin` link upgrades a restored `shepherd` one.
- Not rebuilt: the replica copy (`sync-shadow`), link-only files (`sync-links`), non-primary boards, foreign cards a worker node picks up (their owner delivers), and a link that arrives later for a card this file already held (its subscription may have been retired on purpose).
- 2714: the newest restore wins. A card lost and restored a second time gets a new `restored_at`, so links written after the first restore are covered; before, the first time was kept and those links got no subscription.
- Known gap (named in the module docstring): chats on this gateway are not rebuilt (a same-gateway chat of another profile, or a local `tui` shepherd subscription), because the synced link row has no exact destination for them.

## How it works

- 2713: new `hermes_cli/kanban_restore_chat_subs.py`. `kanban_db_sync._upsert_task` calls `note_restored_task` after inserting a task row; `_upsert_row` calls `restore_link_sub` after inserting a `task_chat_links` row, both inside the apply transaction. `note_restored_task` skips files in the shadow or links directories and non-primary boards, creates the marker table on demand, and records the time. `restore_link_sub` applies the rules above and writes the subscription with `INSERT OR IGNORE` (so a second sync round writes nothing more).
- 2714: `note_restored_task` upserts `restored_at` (`ON CONFLICT ... DO UPDATE`) instead of `INSERT OR IGNORE`, and the docstring names the same-gateway gap.

## Code

`hermes_cli/kanban_restore_chat_subs.py` (new), `hermes_cli/kanban_db_sync.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_sync_restore_origin_subs.py`. Entry path: `kanban_sync_client.sync_board` and `reconcile` against a fake Board service, with a reprovisioned (emptied) primary board file. Covered: a restored card's origin chat is republished and caught up, a second round is idempotent; exact identities kept, with released, NULL-profile and local-gateway links skipped and owning winning for one chat; a late link for an existing card does not resurrect a removed subscription; reconcile after a rebuild that lost the card restores it; replica and foreign cards get none; a second loss after a newer link restores both (2714). The real hub collector and notifier are **untested** (the test mirrors the collector's query), as are archived, placeholder and headless sessions and the clock-skew boundary.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Only cards restored by the sync apply are covered; a subscription lost any other way is not rebuilt.
- Retired-on-purpose subscriptions stay retired for cards the file already held, but a card lost and restored again is treated as a fresh restore.
- `kanban_restored_cards` is local-only and is not synced.

## Plugin

Core change; no plugin required.
