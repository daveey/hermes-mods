# Sessions: GUI chats stay visible across local routing

Mirror snapshot 5406; ordinals 5390.

Entries:

- **5390**: fix(sessions): preserve human GUI visibility across local routing

## Behavior

- `gui` is treated as the desktop surface and creation provenance is consulted only for local routing; explicit deny rules win; the same exclusions are applied in SQL before pagination and totals.

## How it works

- `session_listing.py`, `web_routers/profiles.py`, `web_routers/sessions.py`, `hermes_state_sessions.py`, `tui_gateway/methods_projects.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/session_listing.py`
- `hermes_cli/web_routers/profiles.py`
- `hermes_cli/web_routers/sessions.py`
- `hermes_state_sessions.py`
- `tui_gateway/methods_projects.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/gateway/test_gui_sidebar_provenance.py`
- `tests/hermes_cli/test_session_surface_lists.py`
- `tests/hermes_cli/test_session_surface_visibility.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- No relabeling or backfill of existing sessions.

## Why core instead of a plugin

Session classification is core state.
