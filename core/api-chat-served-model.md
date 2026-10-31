# Chat completions identify the served runtime

Patches: 1351 (`5374347b174`), 1352 (`0d3f82dab0c`).

## Behavior

A non-streaming `/v1/chat/completions` response to a class-selected request
names the concrete provider and model that served the turn, rather than leaving
clients to attribute usage to a selector such as `class:deep`.
The HTTP regression verifies an Anthropic class route reports its actual tuple.

## How it works

The API turn runner adds `served_runtime` with cleaned `provider` and `model`
identifiers from the agent. Patch 1352 extracts response assembly into
`_apply_served_runtime` and limits both recorded identifiers to 80 characters.
Non-empty served fields overwrite the response's corresponding fields.
If a served model is missing, the response keeps its original requested model;
that fallback and identifier sanitization are untested by the added regression.

## Code

- `gateway/platforms/api_server.py`: attach `served_runtime` to the turn result.
- `gateway/platforms/api_server_openai_routes.py`: `_apply_served_runtime` and
  non-streaming completion response assembly.

## Tests

`tests/gateway/test_api_class_routing.py::test_completions_http_class_reply_names_served_provider_and_model`
posts `class:deep` through an aiohttp test server and asserts HTTP 200 plus
`provider=anthropic` and `model=anthropic-deep`. The paid-default resolver is
forbidden and the fixture supplies the native transport.
The test was inspected, not executed for this documentation task.

## Applying

Apply 1351 then 1352 with the preceding routing/API patches in series order.
No new configuration is introduced.

## Gotchas

This is non-streaming chat-completions metadata, not proof of SSE or
`/v1/responses` attribution. Those paths are untested by these patches.
No plugin is required; clients can read the response fields directly.
