# Phantom-ref checks consult the host's Board replica

Patches: **2645**.

## Behavior

- On a Board-synced host the live board file holds only part of each board (its own cards and the copies it claimed); the Board replica holds all of it. The completion gate for `created_cards` and the summary scan for card ids used to look only at the live file (and, for the scan, the hub replica, which can answer "fresh" yet lack a card minted minutes earlier), so real cards were refused or flagged as phantom.
- Now both look the ids up in every Board replica file under `kanban/sync-shadow/` first, read-only. A missing replica file is never created and an unreadable one is skipped.
- The gate keeps its trust rule: a card found only in a replica counts as created by the completer only if its `created_by` is the completer's assignee or id, or the replica links it as the completer's child. Merely existing in a replica is not enough.
- An id absent from the live board and every replica is still flagged by the summary scan and still refused by the gate. Naming a real card in a summary is fine.
- Ids are resolved across several replicas (the second is asked for what the first lacked); with nothing to look up no replica is opened.

## How it works

New `hermes_cli/kanban_board_replica_refs.py`: `replica_tasks(ids, parent)` returns `({id: created_by}, linked)` from each replica file opened read-only; `unresolved_after_replica(missing)` first drops ids a Board replica knows, then applies the existing hub-replica check (returning nothing when that replica cannot say). `_verify_created_cards` in `kanban_db.py` adds the replica results to the found cards and linked children; the old `unresolved_after_replica` moves out of `kanban_db.py` and is re-imported from the new module.

## Code

`hermes_cli/kanban_board_replica_refs.py` (new), `hermes_cli/kanban_db.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_board_replica_refs.py`. Entry path: `kanban_db.complete_task` with `created_cards` and with a summary naming ids, the `suspected_hallucinated_references` event and the `prose_phantom_refs` diagnostic from `kanban_diagnostics`, and the helper `replica_tasks` directly, on a simulated Board-synced host (fresh hub replica without the cards, Board replica with them). Covered: a child created on another node and linked, a card only the Board replica holds named in a summary, an id found nowhere still flagged and refused, a replica card by another profile still phantom as a created card, creator-by-id and replica-link verification, a broken replica skipped, ids spread over two replicas, no replica opened when nothing is asked, and no replica file created. The CLI and tool wrappers around completion are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A replica is only as fresh as its last sync; a card minted since the last pull is still unknown here.
- Replica lookups read every `*.db` in the sync-shadow directory, so a host with many boards opens several files per check that has ids to resolve.

## Plugin

Core change; no plugin required.
