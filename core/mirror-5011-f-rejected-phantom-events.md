# Retire audited fenced phantom event uploads

Mirror snapshot 5011; ordinals 4922.

Entries:

- **4922**: fix(kanban-sync): retire audited fenced phantom event uploads

## Behavior

- Live-claim restoration tombstones a phantom lifecycle event. Generic rejection resolution used to veto that tombstone forever.
- A narrow primary-only retirement is allowed when fresh never-existed Board evidence, a matching rejection audit, and compare-and-delete local revalidation all agree.
- Audit, tombstone and worker lease stay untouched. Historical intent is never replayed.

## How it works

- `hermes_cli/kanban_sync_rejected_event.py` and `kanban_sync_rejections.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_sync_rejected_event.py`
- `hermes_cli/kanban_sync_rejections.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_rejected_phantom_event.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Every retirement predicate fails closed. A provenance race does not retire.

## Plugin?

None.
