# Per-profile `sessions.sidebar_exclude_sources`

Patches:

- `patches/0154-feat-sidebar-per-profile-sessions.sidebar_exclude_so.patch`: a profile lists session sources
  that stay out of the Desktop sidebar's project tree.

What you see: a profile that serves another app's LLM calls (an OpenAI-compatible `api_server` backend) can write
hundreds of sessions a day. Since the cross-gateway sidebar reads every remote profile's tree
([multi-gateway-projects.md](multi-gateway-projects.md), `0149`), those rows flooded it. With one line in that
profile's `config.yaml` they disappear from the sidebar, everywhere it is listed.

```yaml
# <that profile's home>/config.yaml
sessions:
  sidebar_exclude_sources: [api_server]
```

## Behavior

- The project tree (`projects.tree` RPC and the all-profiles `GET /api/profiles/projects/tree`) always leaves out
  `cron`, `kanban` and `oneshot` sessions (upstream). It now also leaves out the sources listed in
  `sessions.sidebar_exclude_sources`.
- The list is read from the config of the profile whose sessions are being listed, like
  `sessions.show_subagents`. In the all-profiles tree each profile applies its own list: a machine profile hides
  its own `api_server` rows, while another profile's `api_server` rows stay visible.
- Accepts a YAML list or a comma-separated string. Unset, empty, unreadable or another type → `[]` (upstream
  behavior). Default `[]` in `config_defaults.py`.
- Only the sidebar project tree is filtered. The sessions still exist, and other listings (dashboard,
  `session.list`, search) are unchanged.

## How it works

- `hermes_cli/session_listing.py`: `sidebar_excluded_sources(hermes_home)` sets a temporary home override,
  reads `sessions.sidebar_exclude_sources` with `load_config_readonly()`, and normalizes it to a list of strings.
- `tui_gateway/methods_projects.py`: `_project_tree_excluded_sources()` = the built-in
  `_PROJECT_TREE_EXCLUDED_SOURCES` plus that list (deduped), read under the caller's home scope.
  `_project_tree_inputs` passes it as `exclude_sources` to `db.list_sessions_rich`.
- `hermes_cli/config_defaults.py`: documents the key under `sessions` with default `[]`.

## Code

`hermes_cli/session_listing.py`, `tui_gateway/methods_projects.py`, `hermes_cli/config_defaults.py`.

## Tests

`tests/hermes_cli/test_profiles_sidebar_scope.py::test_profile_sidebar_exclude_sources_hides_its_machine_sessions`:
through `GET /api/profiles/projects/tree`, a `worker` profile with `[api_server]` hides its own `api_server` row,
keeps its `cli` row, and the `default` profile's `api_server` row stays. The single-profile `projects.tree` RPC
path and the comma-string form are untested.

## Applying

Standalone (upstream already has `_PROJECT_TREE_EXCLUDED_SOURCES` and `show_subagent_sessions`). Most useful with
`0149`, which makes remote profiles' trees visible.

## Gotchas

- The key goes in the profile that writes the machine sessions, not in the viewing profile or the Desktop.
- On a remote gateway, the change applies once that gateway runs this patch; older gateways ignore the key.
- `api_server` is the source name of the OpenAI-compatible API server; check `sessions.source` in that profile's
  `state.db` for the exact value before listing it.

## Plugin?

No. The project tree's source filter is built inside the core RPC/REST handler; no plugin hook sees or filters
its query.
