# Session source tags, refusal copy and deferred class routing

Patches: 1142 1144 1149 1152 1153.

## Behavior

- CLI create-refusal copy includes contract `data.fields`, so invalid input identifies the refused fields rather than looking like an unspecified model-connection failure (1142).
- A managed class-tier launch retains provider `auto`, drops concrete endpoint pins inherited from the profile, and defers class routing until the agent has its durable identity (1144).
- A well-formed free lowercase `--source` tag uses the tool surface while retaining its stored source label. Reserved routing/platform/surface labels remain guarded; class routing is deferred to the agent (1149).
- An inherited `HERMES_SESSION_SOURCE=kanban` does not become a descendant chat’s create source: without an explicit source, the label is `oneshot` for `-q`, otherwise `cli` (1152).
- A CLI `-m class:<level>` selection does not inherit the profile’s concrete provider; without an explicit provider selection it uses `auto` (1153).

## How it works

Canonical create contracts and session policy distinguish a stored label from an admission surface. Worker construction leaves class resolution to `prepare_agent_route`, while explicit forbidden key/URL combinations still refuse. CLI initialization separately handles provider defaults and session-store source selection.

## Code

- `hermes_cli/cli_chat_error_copy.py`
- `gateway/session_policy.py`
- `gateway/session_worker_construct.py`
- `gateway/run_turn_prepare.py`
- `agent/managed_worker.py`
- `tui_gateway/contracts/canonical_sessions.py`
- `hermes_cli/gateway_session_store.py`
- `hermes_cli/cli_init_mixin.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_create_refusal_fields_copy.py`
- `tests/gateway/test_kanban_class_tier_route.py`
- `tests/gateway/test_session_create_free_source.py`
- `tests/gateway/test_policy_class_route_deferred.py`
- `tests/gateway/test_session_policy_refusal_fields.py`
- `tests/hermes_cli/test_gateway_session_store.py`
- `tests/hermes_cli/test_cli_provider_resolution.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

Free tags do not grant dispatcher admission or platform credentials. Unit policy and CLI tests are not a live account-pool allocation or an external `execute_code` transport test.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
