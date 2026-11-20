# Origin ledger: quiet archives and removed-owner re-homes

Mirror snapshot 5011; ordinals 4886, 4887, 4888, 4923, 4924, 4925.

Entries:

- **4886**: Origin ledger: an owner's explicit archive stays closed when the owner goes quiet
- **4887**: test(kanban): prove quiet archive outcome after child ends
- **4888**: test(kanban): pin owner-evidenced archive without scope inventory
- **4923**: Origin ledger: an owner the Board removed keeps its verified completions settled; a re-homed card drops the old binding
- **4924**: Origin ledger: removed-owner re-home lookup by exact keys; close the replica on a failed read; pin the review's mutants
- **4925**: Origin ledger: find a re-homed card's replacement without relying on announced scopes

## Behavior

- **Quiet archive** (4886–4888): an owner's explicit archive stays closed (`closed_uncompleted` if unverified) when the owner goes quiet. A non-terminal owner going quiet still waits. An archive not yet seen stays unknown until catch-up. Owner-evidenced archive can settle without a scope inventory. A leftover hub subscription for an archived card publishes no owner link.
- **Removed owner** (4923): a Board-removed owner keeps verified completions settled; unverified completions stay unknown; an open card stays unknown. An offline owner that still has an info row is not removed. A stale replica proves no removal.
- **Re-home** (4923–4925): a card published by another gateway drops the old binding. Lookup uses exact primary-key reads, not a full replica GLOB. A failed listed-gateways read still closes the replica. A replacement is found even when the new owner announces no scope for the board, has no info row, or announces another board. A verified completion is never settled while another gateway publishes the same card running.

## How it works

- `hermes_cli/kanban_origin_ledger.py` (`project`, `_quiet_owner`, `_removed_owner`, `_Publications`).

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_origin_ledger.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_archive_outcome.py`
- `tests/hermes_cli/test_kanban_chat_links_owner_hub_subs.py`
- `tests/hermes_cli/test_kanban_origin_evidence.py`
- `tests/hermes_cli/test_kanban_release_survives_owner_offline.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Settled needs a fresh replica. A gateway that does not publish the replacement is not enough to drop a running card's binding.
- Unverified archives are closed, not released as done.

## Plugin?

None.
