# Scoped Desktop creation provenance for Kanban ownership

Mirror entries: [2304](patches/2304-daveey-fix-kanban-classify-local-Desktop-owners-by-scoped-creation-provenance.patch), [2677](patches/2677-daveey-Kanban-another-gateway-s-Desktop-local-hash-c.patch).

## Behavior

- Retain interactive Desktop/CLI/TUI/ACP/GUI owners whose local transport IDs resemble headless receipts.
- Use gateway/profile-scoped creation provenance for creator links, reconciliation, replica task lists and notice fanout.
- Suppress known machine sessions and unknown receipt-shaped hashes; a foreign reference never borrows a local session's proof.

## How it works

kanban_session_provenance resolves source and created_source, with machine creation winning over apparent transport. Explicit profiles read only their store; legacy unqualified IDs search local profiles conservatively and treat conflicting proof as unknown. Reads are read-only, avoid store creation/migration and cache readable results for 300 seconds. Link and notification callers share the classifier. Fanout trusts the supplied destination store rather than the ambient process profile.

## Code

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db_notify.py`
- `hermes_cli/kanban_session_provenance.py`
- `tui_gateway/notice_fanout.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_desktop_local_owners.py` — `test_desktop_hash_is_not_headless`, `test_local_row_cannot_prove_foreign_provenance`, `test_ledger_keeps_desktop_origin_and_shepherd`, `test_ledger_profile_is_not_ambient_profile` (additional cases in this file).
- `tests/hermes_cli/test_kanban_desktop_local_replica.py` — `test_desktop_ledger_link_reaches_task_list_and_active_count`.
- `tests/hermes_cli/test_kanban_session_provenance.py` — `test_interactive_local_surfaces`, `test_machine_creation_wins`, `test_foreign_malformed_and_conflicting_refs_never_read_local_store`, `test_legacy_store_and_cache_expiry` (additional cases in this file).

## Follow-up 2677: another gateway's Desktop local-hash chat owns the cards its gateway links to it

Mirror entry: [2677](patches/2677-daveey-Kanban-another-gateway-s-Desktop-local-hash-c.patch).

### Behavior

- Before 2677 a gateway treated every *foreign* `local-<sha256>` session as a machine session: it cannot read the
  other gateway's `state.db`, and a Desktop chat and a worker receipt share that id shape. So a laptop Desktop chat
  that adopted a hub card through the courier owned that card (the courier sub skips the check) but none of its
  descendants.
- Now such a chat (for example `laptop/default/local-…`) counts as an owner on this gateway only when this board
  holds an **unreleased `created` `task_chat_links` row** to that exact gateway/profile/session on the card or on a
  creator/dependency ancestor. That row is the chat gateway's own answer: its reconcile writes an origin card's
  `created` link only for an interactive chat and releases links to its own machine sessions.
- What proves nothing: a courier binding or a `reason: origin` hub sub alone (create/adopt default the origin to the
  caller's session id, which inside a Kanban worker is the worker's own hash, and neither row is ever released);
  observer links, released links, unqualified or profile-less refs, non-hash ids, a link for another profile or
  gateway, and this gateway's own sessions (they keep their `state.db` verdict, so local worker, cron, one-shot and
  subagent hashes stay suppressed). No id-shape guess, no network read, no new store.
- Readers that now accept a proven foreign chat, each only after the existing machine verdict says "machine":
  `kanban_creator_chats.resolve_chats` and `initialize_ownership` (worker-created children, explicit origin),
  `kanban_db_notify._machine_sub_chats` / `_inherit_notify_subs` (dependency children inherit the owner's sub) and
  `add_notify_sub` (a `hub` sub for a proven chat is accepted).
- The chat's own gateway now releases a hub-written `created` link to its own worker hash
  (`kanban_chat_links._owner_written_releases` no longer skips `local-<sha256>` workers as shepherds), and
  `reconcile` keeps that released row instead of retiring it, so the release record survives the next pass and the
  stale link stops proving ownership for new descendants.

### How it works

`hermes_cli/kanban_session_provenance.py` adds `proven_foreign_chats(conn, task_ids, local_gateway=None,
max_depth=16)`: a bounded, cycle-safe ancestor walk over `task_links` parents and the `creator_task_id` of each
card's `created` event, then one query for `kind='created' AND released=0` chat links on those cards, returning
`gateway/profile/session` refs whose gateway differs from the local one and whose session matches the headless
hash shape. An unknown local gateway or a board without `task_chat_links` yields the empty set.

### Code

- `hermes_cli/kanban_session_provenance.py` (`proven_foreign_chats`, `_ancestors`)
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db_notify.py`
- `hermes_cli/kanban_chat_links.py` (`_owner_written_releases`, `reconcile`)

### Tests

Source coverage, **not run in this mirror**: `tests/hermes_cli/test_kanban_foreign_desktop_owner.py` (27 cases per
the commit message): worker-created and dependency children of a bound card get the foreign Desktop owner; a
`created` link proves without a binding; a proven hub sub is accepted and an unproven one refused; explicit foreign
origin with ancestor proof; a courier-bound worker receipt owns nothing; released links stop owning new
descendants; a foreign hash without proof, a link for another ref, profile or gateway, bindings/subs/unqualified
links, foreign non-hash rows and local machine hashes stay excluded; an unknown local gateway proves nothing; and
`test_chat_gateway_releases_hub_written_link_to_its_worker_hash` runs the chat gateway's real `reconcile` on a replica
over two passes. Entry path: the module APIs above against a temporary board. A live courier adoption across two
real gateways and the Board sync of the release row are **untested** here.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

A local- transport prefix alone proves neither human interaction nor headless execution. Remote/ambiguous provenance is not upgraded to interactive by guessing. Store readability and cached provenance do not authorize unrelated sessions; no cross-gateway lookup is performed here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
