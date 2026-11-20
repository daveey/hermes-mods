# Workspace source protocol 2 advertisement

Mirror snapshot 5011; ordinals 4871.

Entries:

- **4871**: feat(kanban): advertise workspace source protocol 2

## Behavior

- Advertise workspace source protocol 2 only inside enabled dispatcher grant calls.
- Default-off configuration, CLI omission, old-Board suppression and v1 attachment defaults stay as they were.
- Nested and exceptional acquire-context restoration is covered.

## How it works

- `hermes_cli/kanban_source_board.py` emits the protocol when the dispatcher grant is enabled.
- `docs/workspace-source-v2-artifacts.md` describes the v2 artifacts.

## Code

Shipped non-test paths (union over these entries):

- `docs/workspace-source-v2-artifacts.md`
- `hermes_cli/kanban_source_board.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_source_launch.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Enabling the protocol is a dispatcher grant, not a global default. Do not treat CLI omission as a protocol advertisement.

## Plugin?

None.
