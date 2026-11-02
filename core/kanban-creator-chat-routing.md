# Worker-created cards route to the real owning chat

Patches:
- `patches/1549-fix-kanban-route-worker-created-cards-to-real-owning.patch` (`1a92241da8b`, `fix(kanban): route worker-created cards to real owning chats`)
- `patches/1550-fix-kanban-preserve-local-origin-delivery-and-unsync.patch` (`3cbed097587`, `fix(kanban): preserve local origin delivery and unsynced reads`)

Related: chat-link sync and placement in [kanban-sync-links-and-placement.md](kanban-sync-links-and-placement.md); placeholder sessions in [kanban-placement-placeholders.md](kanban-placement-placeholders.md).

## Behavior

A card created by a Kanban worker carried the worker's headless execution session as its `created` chat link, so its notices and wakes had no human chat to land in. Now:

- **Inherit the human owner.** When a worker (`creator_task_id`) creates a card, the nearest unreleased human chat links are copied to it: walk the creator, then its creator edge and dependency parents, breadth-first, up to 16 levels, stopping at cycles. Headless (worker/cron) and placement-placeholder sessions are skipped. Owners on other gateways (via `task_chat_links` or a legacy `hub` origin subscription) count.
- **Delivery is copied, not replayed.** The owner's matching `hub`/`tui` notify subscription is copied with every delivery column (mode, metadata, notifier profile); its `last_event_id` starts at the new card's latest event, so old events are never delivered.
- **Explicit origin wins.** `kanban_create(origin_session=...)` or `hermes kanban create --origin-session` overrides inheritance: the card's `session_id` is cleared, a `created` link and an `origin` notify subscription point at that chat. Since 1550, an origin on this gateway and active profile is subscribed as `tui/<session>` (local delivery); other gateways or profiles use `hub/<gateway>/<profile>/<session>`. A headless explicit origin gets no link or subscription.
- **Gaps are recorded.** No human owner found → one `creator_chats_unresolved` event (`task_id`, `creator_task_id`, `max_depth`), no link, no subscription.
- **Headless links are never chats.** Any link on a headless session (formerly only `shepherd` kinds) is excluded/retired; provenance stays in task events.
- **Backfill.** Each `kanban_chat_links.reconcile` first runs `backfill`: legacy cards with a headless/placeholder (or no) `session_id`, a `creator_task_id`, and no `creator_chats_resolved` event are resolved once; unresolved ones stay retryable.
- **Unsynced boards (1550).** `_local_links` skips the resolved-link query when the board has no `task_chat_links` table instead of raising.

## How it works

New `hermes_cli/kanban_creator_chats.py`: `initialize_ownership` (called from `kb.create_task` in the creation transaction), `resolve_chats`, `_chat_links`, `_copy_delivery`, `inherit_chats` (called from `kanban_db_graph.inherit_creator_origin`) and `backfill`. `creator_chats_resolved` marks a card done; `_local_links` publishes `created` links only for such cards. `create_task` gains `chat_origin`; the tool and CLI pass the validated origin ref.

## Code

`hermes_cli/kanban_creator_chats.py` (new), `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_graph.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`.

## Tests

From the patches; not run for this mirror. Each tool/CLI case is parametrized over both entry paths.

- `tests/tools/test_kanban_worker_created_chats.py` (new): `test_explicit_origin_overrides_worker_inheritance` (remote and same gateway, non-active profile → `hub`), `test_unresolved_worker_records_gap_without_a_chat`, `test_human_created_card_keeps_chat`, `test_worker_creation_resolves_remote_ancestor` (with and without a projected link, delivery columns preserved), `test_worker_creation_resolves_same_gateway_chat`.
- `tests/hermes_cli/test_kanban_creator_chat_backfill.py` (new): `test_reconcile_backfills_worker_created_links_without_replay` (worker and placeholder sessions; no replay of old events, idempotent, future events delivered).
- `tests/hermes_cli/test_kanban_chat_links.py`: worker `created` links are no longer kept.
- `tests/hermes_cli/test_kanban_origin.py::test_explicit_origin_argument_and_same_gateway_origin` (1550): same-gateway origin → `tui/<session>`; tool path only, CLI untested.
- The missing-`task_chat_links` guard (1550) has no dedicated test (untested).

## Applying

Depends on the preceding series (chat-link sync, origin parsing, placement placeholders). Apply the full ordered series per [README.md](README.md) "Applying"; standalone application is not certified.

## Gotchas

- Inheritance takes the nearest level with any owner; farther ancestors' chats are not added.
- The explicit origin clears `session_id`, so the creating worker's session no longer appears on the card.
- Backfill runs inside every reconcile; cards that never resolve are re-attempted each time (the gap event is written once).

## Plugin?

No. Ownership is written inside the core card-creation transaction and the chat-link reconcile; no plugin hook covers either.
