# Unloaded host pass cadence

Mirror snapshot 5011; ordinals 4875.

Entries:

- **4875**: Unloaded host pass every 60 s instead of 30 s

## Behavior

- The unloaded host pass interval is 60 s (was 30 s). Notice grace stays 30 s.
- `HERMES_KANBAN_UNLOADED_SWEEP_S` still overrides when set.

## How it works

- `tui_gateway/kanban_unloaded.py` `INTERVAL_S = 60.0`; leases take that interval.

## Code

Shipped non-test paths (union over these entries):

- `tui_gateway/kanban_unloaded.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/tui_gateway/test_kanban_unloaded_host_lease.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- This only changes host-pass CPU cadence. Later chat-wait entries (4936–4949) also edit this module; apply the full snapshot.

## Plugin?

None.
