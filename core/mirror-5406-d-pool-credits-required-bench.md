# Credential pool: credits_required 429 benches the model until weekly reset

Mirror snapshot 5406; ordinals 5367.

Entries:

- **5367**: fix(pool): an Anthropic credits_required 429 benches the model until the weekly reset

## Behavior

- `extract_api_error_context` retains `details.error_code`; a `credits_required` model cooldown lasts until the weekly reset from the usage cache, never shorter than the error's own reset. With no snapshot, behaviour is unchanged (the default bench).

## How it works

- `agent_runtime_helpers.py`, `agent_runtime_helpers_error_context.py`, `credential_pool_model_cooldowns.py`.

## Code

Shipped non-test paths (union over these entries):

- `agent/agent_runtime_helpers.py`
- `agent/agent_runtime_helpers_error_context.py`
- `agent/credential_pool_model_cooldowns.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/agent/test_anthropic_credits_required_bench.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Depends on the usage cache for the reset time.

## Why core instead of a plugin

Credential pool cooldowns are core.
