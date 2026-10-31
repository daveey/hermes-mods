# Classic CLI quit leaves prompt restoration to the gateway

Patch: 1368 (`632eb9c39d5`).

## Behavior

Classic CLI close skips prompt building/persistence work for a `GatewayAgent`
proxy; the gateway already owns that transcript and system prompt.
The prompt-refresh epoch read is accepted by the gateway session store and
returns locally without an RPC or a prompt-refresh warning.
A parity guard requires agent-loop SessionDB calls to be explicitly classified,
rather than becoming unexpected runtime refusals.

## How it works

`_persist_active_session_before_close` returns early for `GatewayAgent` before
it tries to restore/build a system prompt for the proxy's no-op persistence.
The store adds `prompt_refresh_epoch`, `latest_conversation_role`, and
`get_conversation_root` to `_READS`, and `update_system_prompt` to gateway-owned
writes. `_REFUSED_BY_DESIGN` records intentional refusals with required reasons;
it starts empty. The parity test scans three agent-loop source files for calls
and probes against `_session_db`, including a local `db` alias.

## Code

- `hermes_cli/cli_session_mixin.py`.
- `hermes_cli/gateway_session_store.py`.
- Scanned sources: `agent/conversation_loop.py`, `agent/prompt_refresh.py`, `run_agent.py`.

## Tests

In `tests/hermes_cli/test_gateway_session_store.py`:
- `test_prompt_refresh_epoch_is_a_read_the_store_answers`: real temporary state
  DB epoch, no RPC and no epoch-read warning.
- `test_cli_close_does_not_build_a_system_prompt_for_a_gateway_agent`: real
  close helper with a proxy, no prompt-restoration call and no warning.
- `test_every_session_db_call_in_the_agent_loop_is_classified_by_the_store`:
  source-scan coverage and non-empty reasons for any intentional refusals.

Tests were inspected, not executed for this documentation task.

## Applying

Apply 1368 in mirror series order. No new configuration or plugin is required.

## Gotchas

The close test invokes the helper, not an interactive CLI quit subprocess.
The added classification checks do not separately exercise the two other new
read methods or gateway-owned prompt writes at runtime; those paths are untested here.
