# CLI: a typed empty `-t ''` is a no-tools pick

Patch **2043** (`fff879ec83b9784fd70f1f9887a5ce91081457eb`, `fix(cli): a typed empty -t '' is a no-tools pick, not the profile default [daveey]`). Related: [session-start-toolsets.md](session-start-toolsets.md).

## Behavior

- `hermes chat -Q -t ''` or `-t ,` (an empty or comma-only toolset list) now means "no tools". Previously the empty string was treated like "no `-t`", so `session.create` carried no `toolsets` and the gateway froze the full profile default (`code_execution`, `terminal`, ...). A one-shot drafting call intended to run without tools then called `execute_code`, the single-query guard blocked it, the one-iteration budget was spent, and the run ended partial with exit 1 despite a valid answer on stdout.
- Now the CLI sends `toolsets: []` on `session.create`, keeps `_explicit_toolsets = True` and records `_typed_toolsets = []`. The gateway policy resolves to no toolsets at all.
- An explicit empty pick also excludes the profile's enabled MCP servers and default-on plugin toolsets. Without this, `_get_platform_tools` would fold them into a list naming none of them, and a no-tools session would still get the `tool_search` bridge. The same holds with `bypass=True`.
- Unset `-t` (None) is unchanged: coding posture or the gateway-resolved profile default.
- Empty segments are dropped from non-empty lists (`-t 'web,,terminal'` → `['web', 'terminal']`).

## How it works

`cli._build_cli_from_args` computes `typed = toolsets is not None and not (empty list/tuple)`. A string that is empty after splitting and stripping yields `[]`. `_explicit_toolsets`/`_typed_toolsets` now key off `typed` instead of truthiness. In `gateway/session_policy._resolve_toolsets`, after writing the explicit list into `platform_toolsets`, an empty explicit list returns `set()` immediately, before posture and MCP/plugin folding.

## Code

- `cli.py`: `_build_cli_from_args`.
- `gateway/session_policy.py`: `_resolve_toolsets`.

## Tests

`tests/hermes_cli/test_cli_profile_toolsets.py`:

- `test_typed_empty_toolsets_is_a_no_tools_pick` (`''` and `','`): CLI state, `session.create` params, `build_policy(...).toolsets == ()` and `_policy_tools(...)` empty.
- `test_explicit_empty_pick_skips_default_mcp_servers_and_plugin_toolsets`: a non-empty pick still includes the MCP server, while an empty pick (with and without `bypass`) is `set()`.

The commit message reports 3 failing before the fix and passing after.

```sh
scripts/run_tests.sh tests/hermes_cli/test_cli_profile_toolsets.py
```

Not rerun in this mirror pass. Only the `hermes chat` CLI path and the gateway policy function are tested. Other entry points that send `toolsets: []` (REST/ACP) go through the same `_resolve_toolsets` branch but are untested here.

## Applying

Apply through the snapshot-bound replay in [README.md](README.md). It builds on the session-start toolset and coding-posture handling in `cli.py`/`session_policy.py`.

## Gotchas

- `-t ''` must really reach argv as an empty argument. A shell or wrapper that drops it leaves `-t` unset, which still means the profile default.
- `-t ''` now counts as a typed pick, so it is also checked against a resumed session's frozen toolsets. That resume comparison with `[]` is not exercised by these tests.

## Plugin?

No. Toolset resolution happens in the CLI and gateway policy before plugins contribute tools; a plugin cannot make an empty pick mean "none".
