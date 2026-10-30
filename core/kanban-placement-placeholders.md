# Placement placeholders never route chats or become a child card's origin

Patches:

- `patches/1024-fix-kanban-keep-placement-attribution-out-of-chat-ro.patch`
- `patches/1025-fix-kanban-exclude-placement-placeholders-from-live-.patch`
- `patches/1033-fix-kanban-never-hand-a-placement-placeholder-or-for.patch`

A card the MacBook places on the hub carries the creator placeholder session `kanban-placement-<gateway>`, which is attribution only, not a chat. These patches keep it out of every chat-routing path.

## Behavior

- 1024: `kanban_chat_links`, `kanban_db_notify`, `kanban_origin_adapter`, `kanban_origin_creation`, `kanban_placement`, `kanban_remote_notices` and `kanban_unloaded` treat a placement placeholder as no chat (no notify subscription, link, origin or unloaded-sweep target).
- 1025: live pollers (`kanban_remote_notices`, `session_notifications`) exclude placeholders too.
- 1033 (defect kanban-69709bcf16): a hub worker on a MacBook-placed card called `kanban_create`; `_handle_create` used the parent's placeholder `session_id` as the child's origin, hub refused (`origin_session_missing`) and the rejection reached the Inbox. The parent's session id now goes through `_persisted_session_id` like every other source, and that function rejects placement placeholders (a stray completion notice had left a `kanban-placement-macbook` row in hub's `state.db`, so presence alone is not proof).

## How it works

`kanban_placement.is_placeholder_session` (matches the default `kanban-placement-` creator session or the configured `creator_session` values) is called at each routing entry point; `tools/kanban_tools_placement.py` holds the origin-resolution helper moved out of `kanban_tools.py`.

## Code

`hermes_cli/kanban_chat_links.py`, `kanban_db_notify.py`, `kanban_origin_adapter.py`, `kanban_origin_creation.py`, `kanban_placement.py`, `tui_gateway/kanban_remote_notices.py`, `kanban_unloaded.py`, `session_notifications.py`, `tools/kanban_tools.py`, `tools/kanban_tools_placement.py`.

## Tests

`tests/hermes_cli/test_kanban_placeholder_sessions.py` (5), `tests/tools/test_kanban_placeholder_origin.py` (3; the commit records them red before the fix: origin was the placeholder or a missing chat, a real chat still passes through). Tests were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
