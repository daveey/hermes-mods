# Spaced model ids are refused on every entry path, not just /model

Patch: 0666 (core `0bee5c66da`).

## Behavior

- `hermes chat -m "a b"`, `hermes -z ... -m "a b"` and `--tui -m "a b"` on a cloud provider exit 2 with
  `Model names cannot contain spaces. Pass one model per run.` before any API call.
- Kanban `create_task` and `set_model_override` with a spaced model id on a cloud provider raise `ValueError`
  (also after a provider-less pin is bound by `resolve_pin_provider`).
- Self-hosted providers (`custom`, `ollama`, `vllm`, `lmstudio`, `llamacpp`, `local`) and a user-configured
  `base_url` keep spaced ids. This is the same rule `/model` already applied.
- An Anthropic 404 `not_found_error` whose message is `model: <id>` classifies as `model_not_found`
  (a 404 about another resource stays `unknown`).

## Why

A route probe passed seven model ids as one `-m` value. `/model` refused spaced ids, but the startup flag and
Kanban pins never called that rule: Anthropic answered 404 three times, and on a host with a fallback chain
the probe "passed" on another model.

## How it works

One shared helper holds the rule and each entry point calls it, so they cannot drift.

## Code

- `hermes_cli/models_validate.py`: `model_whitespace_error`, `_normalized_provider`
- `hermes_cli/main.py`: `_reject_startup_spaced_model`
- `hermes_cli/kanban_db.py`: `_reject_spaced_pin`
- `agent/error_classifier.py`: `_is_anthropic_model_not_found`

## Tests

- `tests/hermes_cli/test_model_whitespace_entry_parity.py` runs the same bad and good ids through each path:
  `/model` (`test_slash_model_rejects`), `cmd_chat -m` (`test_cmd_chat_m_rejects_before_any_call`,
  `test_cmd_chat_m_rejects_with_config_provider`, `test_cmd_chat_m_accepts_local_spaced_id`), `-z` oneshot
  (`test_oneshot_z_rejects`), Kanban `create_task` (`test_kanban_create_rejects`,
  `test_kanban_create_accepts_custom_spaced_id`) and `set_model_override` (`test_kanban_set_model_rejects`),
  plus the shared helper (`test_shared_helper_rejects`, `test_shared_helper_allows_local`).
- `tests/agent/test_error_classifier.py`: `test_404_anthropic_not_found_error_on_model_is_model_not_found`,
  `test_404_anthropic_not_found_error_on_other_resource_stays_unknown`.
- Untested: the `--tui` path beyond the shared `cmd_chat` guard, and the dashboard set-model REST route.

## Applying

`git am -3 core/patches/0666-*.patch` after 0001-0665.

## Gotchas

The helper only looks at the provider and `base_url` it is given. A pin with no provider is checked after the
provider is bound, so check order matters if you add another pin path.
