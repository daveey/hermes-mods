# Chat-link reconciliation substep accounting

Mirror snapshot 5011; ordinals 4928.

Entries:

- **4928**: perf(kanban-sync): account chat-link reconciliation substeps

## Behavior

- Nested loop-accounting scopes cover the complete kanban-sync-links pass: shared source inventory, configuration signature, board/input gates, backfill, planning, local and ledger links, owner releases, existing rows, machine releases, chat classification, owner links, and writes. Unchanged-board skips are recorded.
- No cache, freshness, scheduling, release or write policy changes. This is measurement, not a CPU reduction.

## How it works

- `hermes_cli/kanban_chat_links.py` and `kanban_sync_client.py` wrap substeps with existing loop-accounting scopes.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_sync_links_accounting.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- The unchanged-board skip still evaluates shared sources first, so source inspection alone is not the dominant named cost.

## Plugin?

None.
