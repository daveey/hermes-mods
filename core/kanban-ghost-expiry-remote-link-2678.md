# Ghost expiry keeps a remote chat link until the owner verifies the completion

Patches: **2623**.

## Behavior

- A retained remote card link whose owner now republishes the card without this session's link used to be expired as a "ghost" whenever an owner row existed. The origin's chat-link reconcile then released the link. A reprovisioned pool worker restores its cards from the Board but not their chat subscriptions, so it republished cards with an empty session list and six chat links were released in one batch, three of them on cards that were still open.
- Now the retained row expires only when the fresh owner record for the card's exact gateway, profile, board and id is owner-attested terminal (fresh scope heartbeat, hash-bound observation) **and** its observation says the completion is verified.
- An open owner row, or a closed row without verified completion, keeps the retained row as a stale unfinished obligation, so the link stays wanted and unreleased. A verified row of another profile does not match. A card that is later reopened loses its closed mark, and one that later closes verified releases the link.
- The receipt-backed terminal-carry predicates and the missing-open-obligation selector are unchanged. Restoring the chat subscriptions on the producer side is not part of this change.

## How it works

The replica projection records which released cards are also verified (`verified`, a per-card flag derived from the attested record and its evidence) and keeps a `closed` set of their keys beside `released`; the set is dropped on unapply and is empty on a stale replica. `_read_remote_links` reports it through a new `closed_out` argument on both the full read and the incremental projection, and stores it in the read memo so an unchanged read answers it too. `_legacy_remote_links` now excludes a ghost from expiry unless its key (gateway, profile, board, id) is in `closed`; a NULL profile never matches a qualified record.

## Code

`hermes_cli/kanban_replica_projection.py`, `hermes_cli/kanban_session_activity.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_ghost_expiry_open_obligation.py` (new), and updates to `tests/hermes_cli/test_kanban_remote_inventory_cache.py` and `tests/hermes_cli/test_kanban_replica_incremental.py`. Entry path: the module API `kanban_session_activity._legacy_remote_links` and `_read_remote_links` against a fake hub replica database, and `kanban_chat_links.reconcile` on a local board, each on the incremental and the full-read projection. Covered: open statuses and unverified done/archived keep the obligation, a verified terminal row expires it, another profile's verified row does not, an open row later closing verified releases the link, the cache keeps a stale ghost without verified closure, and the incremental projection and memo report `closed`. The sidebar and Desktop UI paths that consume these links, and the hub collector that builds a card's sessions, are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Until the producer side restores subscriptions, a reprovisioned owner's cards keep their stale retained rows on the origin; they clear only once the owner marks the completion verified.
- An owner scope with no fresh heartbeat, or a stale hub replica, never marks a card closed, so nothing expires then either.

## Plugin

Core change; no plugin required.
