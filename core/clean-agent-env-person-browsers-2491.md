# Person-facing browser launches use a clean agent environment

Patches: **2433**.

## Behavior

Remaining in-process `webbrowser.open` callers now open the person's browser through `hermes_cli.top_level_env`, so the browser (and anything it cold-starts) does not inherit the agent markers `HERMES_AGENT`/`AI_AGENT`. Without this, notes a person typed in a browser started from an agent-marked terminal could be recorded as agent-authored (see [kanban-http-comment-provenance-2409b.md](kanban-http-comment-provenance-2409b.md)). Affected sign-in and open flows: the Anthropic OAuth login, OpenAI Codex browser login, OpenRouter loopback login, generic OAuth PKCE plugin login, device-code instructions, anonymous challenge presentation, Portal subscription open, billing open, modal connection open, MCP OAuth authorization announce, and the Photon device login (which opens a new tab). Callers that report success (`anon_challenge`, Codex, OpenRouter, device flow, billing, Portal, MCP OAuth) now report the clean child's result.

## How it works

`top_level_env.url_opener_command(url, new=0)` builds an argv that runs `webbrowser.open(url, new=...)` in a fresh isolated interpreter. `open_url_for_person(url, new=0)` spawns it under `clean_launch_env()` without waiting; `open_url_for_person_checked(url, timeout=15.0, new=0)` returns True when the child exited 0 or is still running at the timeout, False on failure or spawn error. Callers import `top_level_env` late and replace direct `webbrowser.open`. `anon_challenge` now catches only `OSError` (no `webbrowser.Error`). The `kanban_http_comment_writer` docstring was updated, and `scripts/boundary_lint_baseline.json` gained an entry for the Photon plugin's new `hermes_cli.top_level_env` import. The OpenRouter A/B eval harness now stubs `top_level_env.open_url_for_person_checked`.

## Code

`hermes_cli/top_level_env.py`, `agent/anthropic_credentials.py`, `hermes_cli/anon_challenge.py`, `auth_codex_browser.py`, `auth_device_flow.py`, `auth_oauth_pkce_plugin.py`, `auth_openrouter.py`, `cli_billing_mixin.py`, `cli_modal_mixin.py`, `portal_cli.py`, `kanban_http_comment_writer.py`, `plugins/platforms/photon/auth.py`, `tools/mcp_oauth.py`, `evals/openrouter_pkce_ab/harness.py`, `scripts/boundary_lint_baseline.json`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): new `tests/hermes_cli/test_clean_person_openers.py`, plus updated `tests/agent/test_anthropic_oauth_pkce.py`, `tests/hermes_cli/test_anon_challenge.py`, `test_auth_codex_browser_login.py`, `test_auth_commands.py`, `test_open_url_for_person.py`, `test_plugin_oauth.py`, `test_subscription_cli.py`, `tests/providers/test_oauth_pkce_plugin.py`, `tests/tools/test_mcp_oauth.py`. These cover each caller above (Codex, OpenRouter, PKCE plugin, device code, anon challenge, Portal, billing, modal, MCP OAuth, Anthropic OAuth, Photon) with a stubbed opener, plus the `new` window flag forwarding. The OpenRouter A/B harness is untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Launchers outside Hermes can still propagate markers to a browser.
- `open_url_for_person` is fire-and-forget; callers needing an outcome use the `_checked` variant.
- The checked variant treats a still-running child at 15 s as success.

## Plugin

Core change; no plugin required.
