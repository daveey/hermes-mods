# Fork callers follow names upstream renamed in the merge

Patches: 0981, 0994, 1056, 1037, 1058, 1062, 1051.

## Behavior

- Kanban skill checks (`kanban_capabilities`, `kanban_skill_check`) import upstream's now-public `locate_skill`, `skill_search_dirs`, `find_all_skills` instead of the underscore names, so they stop raising `ImportError` (981).
- `browser_agent_lifecycle` reads the CDP override from `tools.browser_session_cdp`; the module it used before was deleted with upstream's built-in browser tools (981).
- The fail-closed managed-SDK auth guard accepts a custom `Authorization` from `default_headers`, otherwise the SDK's `auth_headers`, because openai 2.54 no longer folds the key into `default_headers` (994).
- The skill read-seeding guard finds the file `skill_view` served without the removed `_source_path` field (1056).
- `handle_ws` over a bare ASGI scope no longer crashes on `WebSocket.app` (1037).
- The desktop ancestor-commit build reuse rebuilds when a build at HEAD would bake a different stamp (1058).
- `_set_worker_pid` always records the claimed run's worker and treats a `current_run_id` blanked by a sync pull as this claim's (1062).
- Five intentional detached spawns carry a `# detached-spawn: ok` marker with a reason so upstream's spawn checker accepts them (1051).

## How it works

981 is a rename follow-through plus deletion of tests for removed built-in tools. 994 reads the override from `default_headers` first, then falls back to `auth_headers`, keeping the guard fail-closed. 1056 adds a ContextVar (`_served_paths_capture`) that the read mark appends to; `_current_skill_view` uses `payload["_source_path"]` if present, else the last captured served path. 1037 reads `scope.get('app')` and catches `KeyError` from `ws.app` (starlette raises `KeyError`, not `AttributeError`). 1058 keeps the fork rule (ancestor commit with identical desktop inputs is reusable) but, when the stamp commit is not HEAD, asks upstream's `_stamp_drift`; any drift forces a rebuild, unreadable drift keeps the fork rule. 1062 widens the `UPDATE` predicate to `current_run_id IS ? OR current_run_id IS NULL` and updates `task_runs` when a run id is known. 1051 only adds comments.

## Code

- `hermes_cli/kanban_capabilities.py`, `hermes_cli/kanban_skill_check.py`, `tools/browser_agent_lifecycle.py` (981)
- `agent/model_routing_auxiliary_guard.py` (994)
- `tools/skill_manager_guards.py` (1056)
- `tui_gateway/ws.py` (1037)
- `hermes_cli/main_desktop.py` (1058, 1051)
- `hermes_cli/kanban_db_dispatch.py` (1062)
- `hermes_cli/kanban_db_recover.py`, `kanban_placement.py`, `kanban_wake_check.py`, `session_handover.py` (1051)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. 981 edits `tests/tools/test_browser_agent_lifecycle.py`, `test_browser_agent_rework.py`, `test_browser_agent_tabs.py` and removes `tests/tools/test_browser_managed_all_tools.py` content (removed test names include `test_navigate_snapshot_click_flow`, `test_rotated_capability_reattaches_session`, `test_lease_refusal_is_a_tool_error_naming_the_tool`). 994's message cites `test_managed_responses_wire` (incl. header/credential mutation cases) as green; that is the author's claim, not run here. 1062 edits `tests/gateway/test_session_kanban.py`, `test_kanban_finite_admission.py`, `tests/hermes_cli/test_config_version_stamp_cache.py` and `tests/tools/test_skill_frontmatter_index.py` to the new names. 1037's message cites `test_tui_gateway_ws_real_socket`. 1056, 1058 and 1051 add no tests.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

The rename follow-through is only valid against an upstream that already has the public names; on an older base it would break. 1051's markers only silence a checker; they do not change spawning. 1058 depends on upstream's private `_stamp_drift` and `_run_git`. 1062 claims no new test.

## Could this be a plugin instead?

No: each change patches fork code in core modules to match upstream internals.
