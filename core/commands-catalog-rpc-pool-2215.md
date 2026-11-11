# Discover commands without holding the ordered RPC lane

Patch **2212**.

## Behavior

`commands.catalog` runs on the TUI gateway RPC pool instead of inline on a socket's ordered request lane. Skill, plugin and quick-command discovery can finish asynchronously while the lane handles later requests. This removes catalog work as a serial dependency for a new chat's subsequent request; it does not guarantee prompt-start latency under arbitrary load.

## How it works

The method joins `tui_gateway.server._LONG_HANDLERS`, using the existing pooled-handler dispatch mechanism, as `complete.slash` already does. The catalog's result shape and discovery logic are unchanged.

## Code

- `tui_gateway/server.py::_LONG_HANDLERS`: add `commands.catalog`.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/tui_gateway/test_commands_catalog_off_reader.py::test_commands_catalog_is_a_long_handler`: routing-table membership.
- `test_slow_commands_catalog_does_not_hold_the_request_after_it`: a blocked catalog on a controlled RPC pool returns no immediate reply; a following `gateway.ping` answers before the catalog is released, and the catalog result later arrives through the transport.
- The supplied regression uses ping, not an actual Desktop `prompt.submit` flow. End-to-end prompt acknowledgement latency is not measured by this suite or this documentation task.

## Applying

Use only the full ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply this one-line routing change independently of the series. Restart the backend to load the dispatch table.

## Gotchas

Pooled handlers may finish out of request order; callers must correlate replies by request id. Pool contention and slow discovery can still delay the catalog response, even though it no longer blocks the ordered lane inline.

## plugin?

No. Catalog contents can include plugins, but this is core RPC scheduling, not a plugin implementation.
