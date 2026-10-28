# Kanban remote observation scope, replica read path and retained-row evidence

Patches **0461**, **0462**, **0490**, **0497**, **0498**, **0500**, **0501**, **0505**, **0599** (backend) and **0483**, **0484**, **0485**,
**0525** (Desktop). Sources: `4695da764b`, `e90c1654a4`, `236b07480e`, `d121f4c697`, `b23a0d382e`, `12758cf08f`,
`78130169e3`, `ca4285335f`, `5803147af6`, `ffeb8f66fc`, `589534bac5`, `98906dadc7`. Extends
[retention-observation.md](retention-observation.md) and [kanban-all-gateways.md](kanban-all-gateways.md).

## Behavior

- Remote kanban observations are scoped to cards actually linked to the chat: dependency descendants are not claimed
  and not counted as explicit requests; a created child counts through its inherited link; profile-qualified cards are
  distinct and a qualified terminal observation supersedes an unknown blank-profile duplicate (0497/0498/0500).
- Legacy task aliases inherited from an older path are attested with exact graph proofs per hop; a known contradiction
  is not masked by a missing descendant and a malformed path never creates a ledger (0490).
- Owner-scope heartbeat window is 450 s, so a remote release no longer flaps at the 180 s collector period (0461); a
  remembered legacy remote row is not resurrected when its qualified twin is live (0462).
- Linked cards never wait on the remote-links rebuild: readers get the previous projection at once and one background
  rebuild replaces it (only the first projection per scope is built inline; a failed rebuild keeps the previous).
  `session_tasks` rows also carry `activity_kind`/`activity_at`, `note` and `pr_url` (0501). Stale remote-links reads
  apply only to the linked-card strip (0505).
- The remote-links projection is keyed on the hub replica's content, not its file: the `tasks`/`gateway` rows' write
  sequence (`COUNT`, `MAX`, `TOTAL` of `seq`) plus `meta.gateway`, re-read only when hub.db's stat moves. hub-sync
  rewrites ~10k `sessions` rows continuously, so the stat-keyed fingerprint changed on nearly every read and every
  reader re-projected 2.4k cards. The projection's own max age is 15 s (`_LINKS_MAX_AGE_S`; counts keep the 3 s
  TTL), since its fingerprint covers every store but state.db alias tips. A replica without `seq` falls back to the
  file stat (0599).
- Desktop refreshes kanban evidence for retained older sessions outside the recency page on their own owner, fills an
  unannotated detail row without replacing its metadata, clears old counts on an authoritative zero, marks a failed
  read unknown, and rejects another profile with the same session id (0483/0484); freshness-only (remote-stale) row
  changes are published to every slice (0485); preview-only kanban badges refresh on focus with a quiet backstop while
  unfocused (0525).

## How it works

`hermes_cli/kanban_session_activity.py` (projection, cache, scope), `kanban_origin_ledger.py`,
`kanban_origin_adapter.py`, `kanban_origin_legacy.py`; `web_routers/sessions.py` annotates detail rows; renderer side
`app/session/hooks/refresh-retained-kanban.ts`, `use-session-list-actions.ts`, `lib/session-signatures.ts`,
`app/contrib/hooks/use-background-sync.ts`.

## Code

Files above. Tests listed next.

## Tests

`tests/hermes_cli/test_kanban_observation_scope.py`, `test_kanban_activity_scope.py`, `test_kanban_actionability_counts.py`,
`test_kanban_remote_inventory_cache.py` (background re-projection, readers never wait, activity/note/PR fields),
`test_kanban_remote_terminal_release.py`, `test_kanban_origin_scope_heartbeat.py`, `test_kanban_origin_legacy_path.py`,
`test_session_detail_kanban.py`, `test_kanban_session_activity.py::test_hub_session_rows_do_not_invalidate_the_remote_links_projection` and `::test_hub_signature_is_reused_until_the_file_changes_and_falls_back_on_old_replicas` (0599); renderer `refresh-retained-kanban.test.ts`, `use-session-list-actions.test.tsx`,
`use-background-sync.test.ts`. Live multi-gateway behavior is untested here.

## Applying

`git am -3` in number order; 0498/0500 are test-only follow-ups to 0497.

## Gotchas

Do not widen observation scope back to descendants: it inflates counts and makes shepherds claim dependency work.

**Ghost links (0543).** The remembered remote-link snapshot used to keep a (session, card) pair forever once the
replica stopped carrying that link, so a chat whose link was dropped on the owner kept showing the card. Now
`_read_remote_links(..., owners_out=)` collects every (gateway, board, card) whose owner row a FRESH replica carries;
`_legacy_remote_links` drops a remembered pair whose card is in that set but whose link is gone. A stale replica, or
one missing the owner row, still keeps the remembered pair (unknown is not "unlinked"). Tests:
`test_kanban_remote_inventory_cache.py::test_dropped_link_on_fresh_replica_expires_ghost`,
`::test_missing_owner_keeps_stale_ghost`.

## Plugin?

No: core projection and Desktop store code.
