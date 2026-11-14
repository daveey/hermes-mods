# Profile-board ledger fallback boundaries

Mirror ordinals: 3756.

## Behavior

- Lineage-scoped chat-link reads probe only that lineage's profile-board ledger cards.
- Closed-card readers can see released profile-board cards as explicit released rows, matching replica link semantics.
- The links-diff report names trusted profile-board rows but does not trust a missing row when the same owner/profile demonstrably syncs other cards onto that board.

## How it works

`kanban_chat_links._profile_board_cards` filters requested session IDs before carry probes, chunks task lookups in groups of 500, and records `owner_on_board` for all-chat diagnostic reads. Missing profile-board rows stay in the reader's union, but `links_diff` counts an owner's missing row as ledger-only if another card proves the owner is represented in the replica. It emits a per-session `profile_board` mapping as well as its count. The CLI prints that mapping. `_profile_board_index` permits stale/background projection behavior when the last projection carried no such cards, avoiding unnecessary waiting on empty fallback reads.

## Code

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_sync_cli.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_chat_links.py` — The general links-diff expected report includes the new profile_board mapping, pinning its output shape alongside existing counts.
- `tests/hermes_cli/test_kanban_chat_links_profile_board.py` — Assertions contrast trusted fallback rows with missing cards of a syncing profile, verify CLI reporting and non-clean exit, pin no-replica projection reads, inspect lineage-only SQL probes, expose released closed rows, and retain conservative behavior for unreadable probes.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Unreadable replicas do not justify guessing ledger-only ownership. This is a read-side union/report refinement, not migration of profile-owned boards into a shared board.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
