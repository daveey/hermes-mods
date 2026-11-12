# Dashboard comment writer provenance for local agent processes

Patches: **2364–2365, 2386–2387**.

## Behavior

`POST /api/plugins/kanban/tasks/<id>/comments` without `writer_kind` used to record `human` for any token holder. For a raw loopback TCP peer the endpoint now resolves the socket owner and walks its process ancestry with the CLI's comment-writer logic: an agent marker or inconclusive walk records `agent`; clean ancestry and remote/unresolvable peers keep the legacy human default. This is a heuristic, not authentication. Linux and Darwin only. Clean ancestry through a capable same-uid systemd user manager is preserved (2386–2387). Browser/app auto-opens from `hermes dashboard`, `hermes webapp` and `hermes desktop` now use a clean environment so a person's notes typed there are not labelled agent.

## How it works

`web_server_transport.transport_observed_app` records the raw peer outside Uvicorn's proxy middleware so `X-Forwarded-For` cannot launder it. `kanban_http_comment_writer` matches socket owners (global inventory on Linux; per-process fallback on Darwin). `top_level_env.open_url_for_person` opens URLs from a child interpreter with `clean_launch_env()`. 2365 records the one intended core crossing in the boundary lint baseline.

## Code

`hermes_cli/kanban_http_comment_writer.py`, `kanban_comment_writer.py`, `web_server_transport.py`, `web_server.py`, `web_server_lifecycle.py`, `top_level_env.py`, `main_dashboard.py`, `main_desktop.py`, `webapp_launcher.py`, `plugins/kanban/dashboard/plugin_api.py`, `plugin_api_comment_actor.py`, `scripts/boundary_lint_baseline.json`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_kanban_http_comment_provenance.py`, `test_kanban_http_comment_real_client.py` (real token-holding curl records agent; clean curl records human), `tests/hermes_cli/test_kanban_http_comment_writer.py`, `test_kanban_comment_user_manager.py`, `test_web_server_raw_transport.py`, `test_open_url_for_person.py`. Windows is not covered.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Not authentication: an agent that clears its markers or comes from a remote peer is recorded as before. Windows keeps the legacy default.

## Plugin

Core dashboard route that serves the bundled Kanban plugin API; no extra plugin required.
