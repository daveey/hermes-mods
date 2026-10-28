# Bare vendor model ids are refused on OpenRouter and Nous

Patch **0682** (`f359ad12a3`).

## Behavior

After the client is built, a bare vendor model id (`claude-*`, `gpt-*`, and the other known vendor prefixes) on `openrouter` or `nous` raises `ModelProviderMismatchError` before any request. The WARNING names the provider, base URL, model and route (`pinned`, `fallback` or `auto`) and suggests `vendor/model` or `--provider <vendor>`.

- An id that already contains `/` passes. An unknown house id passes.
- A direct vendor provider (Anthropic with a bare `claude-*` id) is unchanged.
- `agent.model_provider_guard: false` turns the guard off.
- API worker threads copy the caller's session id, so `Streaming failed before delivery` in `errors.log` is tagged `[<sid>]`. A thread with no session id stays untagged.

The pin-without-provider route itself is 0653 ([kanban-pin-provider.md](kanban-pin-provider.md)). This guard covers every other route that can still land a bare vendor id on OpenRouter or Nous (auto config, fallback, cron, CLI).

## How it works

`agent/model_provider_guard.py` runs from `agent_init` once the provider is final. `chat_completion_helpers._context_thread_target` copies the thread-local session id into the API worker thread. The Nous provider-parity fixture uses `openai/gpt-5` because every Nous catalogue id is `vendor/model`.

## Code

`agent/model_provider_guard.py` (new), `agent/agent_init.py`, `agent/chat_completion_helpers.py`, `hermes_cli/config_defaults.py`, `website/docs/user-guide/configuration.md`.

## Tests

`tests/agent/test_model_provider_guard.py`:

- `test_bare_claude_on_openrouter_is_refused_before_any_request`
- `test_auto_route_that_lands_on_openrouter_is_named_auto`
- `test_bare_claude_on_nous_is_refused`
- `test_namespaced_or_unknown_ids_pass`
- `test_direct_anthropic_keeps_bare_claude`
- `test_config_switch_turns_the_guard_off`

`tests/agent/test_worker_thread_session_tag.py`:

- `test_worker_thread_logs_carry_the_callers_session_tag`
- `test_worker_thread_without_a_session_stays_untagged`

`tests/agent/test_provider_parity.py` fixture id change (`openai/gpt-5`). This mirror did not re-run these files.

Untested: a live OpenRouter or Nous HTTP call. The unit tests assert the guard raises before the request is sent; they do not dial the provider. Cron and CLI entry points are covered only as routes the guard classifies, not by a cron-process or `hermes -m` process test in this commit.

## Applying

`git am -3` 0682.

## Gotchas

- The guard runs only after the client exists. A failure that happens while choosing the provider never reaches it.
- Turning the guard off restores the old 400 from the provider. Leave it on unless a house id on OpenRouter/Nous uses a known vendor prefix and has no `/`.

## plugin?

No. The check is on the agent init path, after provider resolution, which a plugin hook does not own.
