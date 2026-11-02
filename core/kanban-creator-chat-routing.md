# Worker-created cards route to the real owning chat

Patches: 1549 1550 1605 1613 1614 1615 1623 1624

- `patches/1549-fix-kanban-route-worker-created-cards-to-real-owning.patch` (`1a92241da8b`, `fix(kanban): route worker-created cards to real owning chats`)
- `patches/1550-fix-kanban-preserve-local-origin-delivery-and-unsync.patch` (`3cbed097587`, `fix(kanban): preserve local origin delivery and unsynced reads`)
- `patches/1605-fix-kanban-a-done-remote-card-whose-chat-link-the-or.patch` (`9d13eeb47e7`, `fix(kanban): a done remote card whose chat link the origin released stops counting as open`)
- `patches/1613-fix-kanban-a-worker-s-card-filed-across-board-files-.patch` (`4f3cff8c002`, `fix(kanban): a worker's card filed across board files reaches its creator's human chat`)
- `patches/1614-fix-kanban-read-the-board-replica-through-the-lock-g.patch` (`79c9174719d`, `fix(kanban): read the board replica through the lock guard`)
- `patches/1615-daveey-kanban-explicit-ambient-origin-keeps-parent-A.patch` (`f369dc19f8e`, `kanban: explicit/ambient origin keeps parent ACK subs; headless origin records unresolved`)
- `patches/1623-fix-kanban-the-chat-s-gateway-releases-an-owner-writ.patch` (`3cc6ce09c84`, `fix(kanban): the chat's gateway releases an owner-written created link once the card closes`)
- `patches/1624-fix-kanban-guard-owner-link-replica-connections-dave.patch` (`5eb82ae17d3`, `fix(kanban): guard owner-link replica connections`)

Related: chat-link sync and placement in [kanban-sync-links-and-placement.md](kanban-sync-links-and-placement.md); placeholder sessions in [kanban-placement-placeholders.md](kanban-placement-placeholders.md); the guarded opener that 1614/1624 switch to in [kanban-lockguard.md](kanban-lockguard.md).

## Behavior

A card created by a Kanban worker carried the worker's headless execution session as its `created` chat link, so its notices and wakes had no human chat to land in. Now:

- **Inherit the human owner.** When a worker (`creator_task_id`) creates a card, the nearest unreleased human chat links are copied to it: walk the creator, then its creator edge and dependency parents, breadth-first, up to 16 levels, stopping at cycles. Headless (worker/cron) and placement-placeholder sessions are skipped. Owners on other gateways (via `task_chat_links` or a legacy `hub` origin subscription) count.
- **Delivery is copied, not replayed.** The owner's matching `hub`/`tui` notify subscription is copied with every delivery column (mode, metadata, notifier profile); its `last_event_id` starts at the new card's latest event, so old events are never delivered.
- **Explicit origin wins.** `kanban_create(origin_session=...)` or `hermes kanban create --origin-session` overrides inheritance: the card's `session_id` is cleared, a `created` link and an `origin` notify subscription point at that chat. Since 1550, an origin on this gateway and active profile is subscribed as `tui/<session>` (local delivery); other gateways or profiles use `hub/<gateway>/<profile>/<session>`.
- **1615 (explicit origin keeps parent ACK subs).** An explicit origin, or an ambient `$HERMES_ORIGIN_SESSION`, replaces only the *creator's* ownership. Dependency parents' owning notify subs are still inherited (cursor at the child's latest event), so a parent's chat still hears the child block or finish; parents' observer (`shepherd`) subs are not copied. Before 1615 the explicit-origin path returned before parent inheritance. Tool and CLI, explicit and ambient, are tested.
- **1615 (headless explicit origin).** An explicit origin naming a headless session (a worker/cron receipt) no longer records `creator_chats_resolved` with nothing written. It falls through to normal creator resolution; if nothing resolves, one `creator_chats_unresolved` event (with `headless_origin`) is recorded, so census and backfill can find the card. No link, no subscription.
- **Gaps are recorded.** No human owner found → one `creator_chats_unresolved` event (`task_id`, `creator_task_id`, `max_depth`), no link, no subscription.
- **Headless links are never chats.** Any link on a headless session (formerly only `shepherd` kinds) is excluded/retired; provenance stays in task events.
- **Backfill.** Each `kanban_chat_links.reconcile` first runs `backfill`: legacy cards with a headless/placeholder (or no) `session_id`, a `creator_task_id`, and no `creator_chats_resolved` event are resolved once; unresolved ones stay retryable.
- **Unsynced boards (1550).** `_local_links` skips the resolved-link query when the board has no `task_chat_links` table instead of raising.

### Cross-board creators (1613, 1614)

The walk above only reads the card's own board file. Two cross-board shapes left cards unresolved:

- **1613 (worker node).** A card leased to another machine runs there from a copy in that machine's live board file; the copy carries no chat links, and the creator chain lives only in the board replica (`sync-shadow/<board>.db`). When the local walk finds nothing, `inherit_chats` (and so the reconcile backfill) retries `resolve_chats` on the replica (read-only; skipped when the board has no replica, the replica is this same file, or it lacks `task_chat_links`). On a hit it writes the `created` link, records `creator_chats_resolved` with `source: "replica"`, and writes a fresh `notify` sub (`reason: create`) instead of copying one: `tui/<session>` when the owner's chat is on this gateway, else `hub/<gateway>/<profile or default>/<session>`, cursor at the card's latest event (no replay). Tested for `kanban_create` and `hermes kanban create`, and for backfill of a card filed before the replica existed.
- **1613 (placement).** When `kanban_create` routes a worker's card to another gateway (immediately, or deferred until its parents finish), the origin it sends is the nearest real chat `resolve_chats` finds for the creator — only a chat on *this* gateway with a persisted session (the receiving side binds the origin on the sending gateway), carrying that chat's profile. A foreign-gateway chat is skipped; when nothing resolves and the caller's session is headless, the card is sent with no origin (never the worker transcript). Tool path only: the CLI create never places remotely.
- **1614.** The replica read uses `kanban_lockguard.connect` instead of a raw `sqlite3.connect`.

### Chat links (1605, 1623, 1624)

The owner gateway writes a `created` link for a remote origin chat (`initialize_ownership` / `inherit_chats`) that nothing released: the owner's reconcile manages only its own chats' links, and the origin's only its own board file. Done cards therefore stayed `unknown` obligations in `replica_cards`, and the sidebar's open count named cards `session_tasks` never listed.

- **1605 (read side).** `_read_replica` returns `chat_released` = 1 when another link of the same card and chat (other kind, e.g. the origin's `shepherd` link) is released. `replica_cards` then skips that row if the card is closed (`done`/`archived`). Open cards keep every unreleased link; a missing card row stays `unknown`; another chat's link on the same card is unaffected.
- **1623 (write side).** `reconcile` adds `_owner_written_releases`: it reads the board replica and, for each unreleased `created` link to one of this gateway's chats on a card another gateway created whose replica status is closed, writes the same uid with `released = 1` (the same Board row, pushed through the sync outbox). Links the local/ledger set already carries keep their answer; open cards, local cards, missing card rows, placeholder and headless-worker sessions are skipped. An unreadable replica releases nothing that round. Idempotent across reconciles.
- **1624.** `_owner_written_releases` opens the replica with `kanban_lockguard.connect`.

## How it works

New `hermes_cli/kanban_creator_chats.py`: `initialize_ownership` (called from `kb.create_task` in the creation transaction), `resolve_chats`, `_chat_links`, `_copy_delivery`, `inherit_chats` (called from `kanban_db_graph.inherit_creator_origin`) and `backfill`. `creator_chats_resolved` marks a card done; `_local_links` publishes `created` links only for such cards. `create_task` gains `chat_origin`; the tool and CLI pass the validated origin ref.

- **1615:** `initialize_ownership` parses the origin first. No origin or a headless one → `inherit_creator_origin` + `_inherit_notify_subs`, then the unresolved event if neither a resolved nor an unresolved event was written. A real origin → clear `session_id`, `creator_chats_resolved {explicit_origin}`, `_inherit_notify_subs` for the parents, then the origin link and sub.
- **1613:** `_replica_chats` (replica `resolve_chats`) and `_owner_delivery` (new sub for an owner whose card is not in this file) in `kanban_creator_chats.py`; `_placement_origin` in `tools/kanban_tools_placement.py` returns `{session_id, origin_profile}`, passed into `_place_remote` (→ `create_remote`) and `_marker_placement_fields` (→ `record_deferral`).
- **1605:** an `EXISTS` subquery in `_read_replica`'s link select; a terminal-status skip in `replica_cards`.
- **1623:** `want.update(_owner_written_releases(board, gateway, want))` after the local and ledger links in `reconcile`.

## Code

`hermes_cli/kanban_creator_chats.py` (new in 1549; changed by 1613, 1614, 1615), `hermes_cli/kanban_chat_links.py` (also 1605, 1623, 1624), `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_graph.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py` (also 1613), `tools/kanban_tools_placement.py` (1613).

## Tests

From the patches; each tool/CLI case is parametrized over both entry paths.

- `tests/tools/test_kanban_worker_created_chats.py` (new): `test_explicit_origin_overrides_worker_inheritance` (remote and same gateway, non-active profile → `hub`), `test_unresolved_worker_records_gap_without_a_chat`, `test_human_created_card_keeps_chat`, `test_worker_creation_resolves_remote_ancestor` (with and without a projected link, delivery columns preserved), `test_worker_creation_resolves_same_gateway_chat`. 1613 extracts its `make_home` helper for reuse.
- **1615**, same file: `test_explicit_origin_keeps_parent_ack_subs` (tool and CLI × explicit and ambient `$HERMES_ORIGIN_SESSION`; the parent's owning sub is inherited with cursor at the child's latest event, the `shepherd` observer is not copied, a single origin link), `test_headless_explicit_origin_is_unresolved_not_resolved` (tool and CLI × with and without a creator task; no resolved event, exactly one unresolved, no subs).
- `tests/hermes_cli/test_kanban_creator_chat_backfill.py` (new): `test_reconcile_backfills_worker_created_links_without_replay` (worker and placeholder sessions; no replay of old events, idempotent, future events delivered).
- `tests/hermes_cli/test_kanban_chat_links.py`: worker `created` links are no longer kept.
- `tests/hermes_cli/test_kanban_origin.py::test_explicit_origin_argument_and_same_gateway_origin` (1550): same-gateway origin → `tui/<session>`; tool path only.
- **1613**, `tests/tools/test_kanban_cross_board_creator_chats.py` (new): `test_worker_node_child_resolves_creator_chat_from_replica` (tool and CLI; `hub` sub, cursor at latest event, the worker transcript in the chain is ignored), `test_backfill_repairs_an_unresolved_worker_node_card_from_replica`, `test_placed_worker_child_carries_the_creator_chat_as_origin` (default and non-default profile), `test_placed_worker_child_of_an_unresolved_creator_has_no_origin`, `test_placed_worker_child_skips_a_chat_on_another_gateway`, `test_deferred_worker_child_records_the_creator_chat_for_its_forward`, `test_deferred_worker_child_of_an_unresolved_creator_records_no_origin`.
- **1605**, `tests/hermes_cli/test_kanban_chat_links.py`: `test_a_done_card_whose_shepherd_link_is_released_drops_the_owners_created_link` (done and archived settled; the open card, another chat, and an unreleased sibling stay; `active_task_counts`, `annotate_kanban_activity` and `session_tasks` agree), `test_tui_legacy_subs_on_done_local_cards_count_nothing` (legacy/invalid-metadata `tui` subs count an open local card, never a done one; a test only, no code change in 1605).
- **1623**, same file: `test_reconcile_releases_the_owners_created_link_once_the_card_is_closed` (done/archived released under the same uid and queued in the outbox; open, ledger-held, local, other-gateway and missing-row links untouched; idempotent; `replica_cards` before and after the push).

These were not rerun by the mirror pass.

Untested:
- The missing-`task_chat_links` guard (1550).
- CLI path of the same-gateway `tui/<session>` origin (1550).
- 1613 replica resolution where the owner's chat is on this gateway (the `tui` branch of `_owner_delivery`); tests cover only the `hub` branch.
- 1613 replica skips (no replica, replica is the live file, replica without `task_chat_links`, unreadable replica).
- 1613 placement with a non-headless caller session and an unresolved creator (keeps the caller's session).
- 1615 headless origin whose creator *does* resolve (tests cover only the unresolved outcome).
- 1623 placeholder/headless-worker skips and the unreadable-replica path.
- 1614 and 1624 have no dedicated tests; they satisfy the raw-`sqlite3.connect` lint `tests/hermes_cli/test_kanban_lockguard.py::test_kanban_modules_open_boards_only_through_the_guard` (not part of these patches; see [kanban-lockguard.md](kanban-lockguard.md)).

## Applying

Depends on the preceding series (chat-link sync, origin parsing, placement placeholders, the lock guard). 1614 applies on 1613 and 1624 on 1623 (same functions); 1623 applies on 1605 (same file). Apply the full ordered series per [README.md](README.md) "Applying"; standalone application is not certified.

## Gotchas

- Inheritance takes the nearest level with any owner; farther ancestors' chats are not added.
- The explicit origin clears `session_id`, so the creating worker's session no longer appears on the card.
- Backfill runs inside every reconcile; cards that never resolve are re-attempted each time (the gap event is written once).
- Replica-resolved owners (1613) get a fresh `notify` sub, not a copy of the owner's delivery columns (mode, metadata and notifier profile do not cross board files).
- A placed worker card (1613) only takes a creator chat on the sending gateway; a creator owned only by a chat elsewhere is sent with no origin.
- 1623: a card reopened after its owner-written link was released stays released for that chat; the uid is one row, so the owner's original intent is not recoverable.
- 1605 and 1623 overlap: 1605 hides a closed card on read when a sibling link is released; 1623 releases the `created` link itself when no ledger link covers it. Both depend on a fresh replica.

## Plugin?

No. Ownership is written inside the core card-creation transaction, the placement origin inside `kanban_create`, and link release inside the chat-link reconcile and replica read; no plugin hook covers any of them.
