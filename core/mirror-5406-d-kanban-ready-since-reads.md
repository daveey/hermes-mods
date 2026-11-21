# Kanban: ready_since on linked-card rows and card reads

Mirror snapshot 5406; ordinals 5376.

Entries:

- **5376**: feat(kanban): linked-card rows and card reads carry ready_since for the wait-for-worker age

## Behavior

- Local session task rows, replica rows and `GET /tasks/:id` carry the synced `ready_since` (milliseconds); boards without the column read None.

## How it works

- `kanban_chat_links.py`, `kanban_session_activity.py`, dashboard plugin API.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_session_activity.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_ready_since_rows.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Dashboard API change; older boards return None.

## Why core instead of a plugin

The fields come from core kanban reads.
