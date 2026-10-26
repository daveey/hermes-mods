# Gemini: current default models, and `--provider` without `-m`

Patches:

- `patches/0156-fix-gemini-provider-gemini-without-m-uses-a-Gemini-m.patch`: `--provider gemini` without `-m`
  no longer sends the config's `claude-*` model to Gemini; Gemini's aux and adapter defaults move to
  `gemini-3.8-flash`.

What you see: with an Anthropic model as your default, `hermes chat -q '…' --provider gemini` used to fail three
times with `Gemini HTTP 404: models/claude-opus-… is not found`. It now answers on `gemini-3.8-flash`, and the
session title is generated on Gemini too. Title generation and other auxiliary calls on the `gemini` provider no
longer default to an older flash id.

- `patches/0248-daveey-gemini-list-native-v1beta-models-with-x-goog-.patch`: model listing on the native AI Studio base uses `x-goog-api-key` (Bearer returned 401) and parses the native response shape.

## Behavior

- `fetch_models` on the `gemini` profile (0248): the native `/v1beta/models` endpoint is called with the `x-goog-api-key` header and its native payload shape is parsed. An OpenAI-compatible base keeps Bearer auth, a custom proxy base uses its own `/models`, and a 401 returns `None` without logging the key.

- `--provider X` without `-m`: if the configured model clearly belongs to another vendor and X serves only its
  own vendor's models (`gemini` → Google, `anthropic` → Anthropic), the CLI uses X's default model
  (`get_default_model_for_provider`, `gemini-3.8-flash` for Gemini) and logs
  `--provider gemini without -m: configured model … belongs to another vendor; using …`.
- Left alone: an explicit `-m` (even a foreign one), a same-vendor model (`gemini-3.7-flash`,
  `models/gemini-3.7-flash`, `google/…`), an id with no known vendor (`my-private-finetune`), aggregator and
  custom providers, and a named custom provider's own `default_model` (upstream #86978 path).
- Gemini auxiliary default (`default_aux_model` on the `gemini` provider profile, and the
  `_API_KEY_PROVIDER_AUX_MODELS_FALLBACK` entry) is `gemini-3.8-flash` (was `gemini-3.6-flash`). It applies when
  an auxiliary task (title generation, compression, …) is pointed at `gemini` without a model. With `auto`,
  auxiliary tasks reuse the main model, which the fix above now makes a Gemini one.
- `GeminiNativeClient` default `model` argument: `gemini-3.8-flash` (was `gemini-3.7-flash`).

Why: Google answers 404 "This model models/gemini-2.5-flash is no longer available to new users. Please update
your code to use models/gemini-3.8-flash" for `gemini-2.5-flash`, `-2.5-pro` and `-2.5-flash-lite` on new AI
Studio keys. On 2026-10-20 such a key served `gemini-3-flash-preview`, `3.5`–`3.8-flash`, `3.1-pro-preview`,
`3.1-flash-lite` and `gemini-flash-latest`.

## How it works

- `hermes_cli/model_normalize.py`: `_SINGLE_VENDOR_PROVIDERS` and `model_conflicts_with_provider(model,
  provider)`. True only when the provider is single-vendor and `detect_vendor` (after stripping a matching
  provider prefix) returns a vendor from the known `_VENDOR_PREFIXES` table that differs. Unknown prefixes such
  as `models/` never count.
- `hermes_cli/cli_init_mixin.py`: records `_provider_flag_without_model` (provider given, no `-m`, no custom
  `default_model` applied).
- `hermes_cli/cli_agent_setup_mixin.py` (`_ensure_runtime_credentials`): after the runtime provider is resolved
  and before `_normalize_model_for_provider`, swaps a conflicting model for the provider default and marks it
  `_model_is_default`. Because it runs on the resolved provider, aliases (`google`, `google-ai-studio`) count.
- `plugins/model-providers/gemini/__init__.py`, `agent/auxiliary_client.py`, `agent/gemini_native_adapter.py`:
  the model ids.

## Code

`hermes_cli/model_normalize.py`, `hermes_cli/cli_init_mixin.py`, `hermes_cli/cli_agent_setup_mixin.py`,
`plugins/model-providers/gemini/__init__.py`, `agent/auxiliary_client.py`, `agent/gemini_native_adapter.py`.

## Tests

- `tests/hermes_cli/test_cli_provider_resolution.py`:
  `test_provider_switch_without_model_drops_foreign_vendor_model` (config `claude-opus-5-5`/anthropic,
  `--provider gemini` → `gemini-3.8-flash`), `test_provider_switch_with_explicit_model_keeps_it`,
  `test_provider_switch_without_model_keeps_same_vendor_or_unknown_model` (3 ids).
- `tests/hermes_cli/test_model_normalize.py::TestModelConflictsWithProvider` (conflict and no-conflict tables).
- `tests/agent/test_gemini_aux_default_model.py`: `test_gemini_aux_default_is_current_flash`,
  `test_title_generation_on_gemini_without_model_uses_current_flash` (`resolve_provider_client("gemini", None,
  task="title_generation")` returns a `GeminiNativeClient` on `gemini-3.8-flash`).
- The swap is tested through the CLI (`HermesCLI`) only. Gateway/Desktop sessions take their model from the
  session or picker, not `--provider`, and are untested here. The `anthropic` direction is covered by the
  predicate table only.
- Live check (2026-10-20): `hermes chat -q 'Reply with exactly: ok' --provider gemini` with a `claude-opus-*`
  default replied `ok`; main and title calls both ran on `gemini-3.8-flash` with no 404.

- 0248: `tests/plugins/model_providers/test_gemini_profile.py` (native header and shape, OpenAI-compat keeps Bearer, proxy base, 401 returns None without logging the key). Not re-run when this doc was written (untested here).

## Applying

Standalone. Needs the upstream `gemini` provider profile, `detect_vendor`, and `get_default_model_for_provider`.
When Google moves the default again, change the three model ids and the `gemini` catalog's first entry
(`hermes_cli/models_catalog_static.py`, already `gemini-3.8-flash` upstream).

## Gotchas

- Gemini's TTS default (`gemini-2.5-flash-preview-tts`) is unchanged: it still served new keys on 2026-10-20.
- An explicit `-m gemini-2.5-flash` still 404s on new keys. That is the user's choice, so it isn't rewritten.
- Only `gemini` and `anthropic` are single-vendor here. Add a provider to `_SINGLE_VENDOR_PROVIDERS` only if its
  API can never serve another vendor's ids.

## Plugin?

No. The CLI picks its model in core init and runtime-resolution code; no plugin hook runs before the first
request's model is fixed. The aux default lives in the bundled provider profile.
