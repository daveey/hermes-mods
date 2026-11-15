# Late session inventory and MCP reload reporting

Patches: 3798, 3831.

## Behavior

Late inventories arrive after the pending create/resume reply, failed builds settle with empty inventory rather than staying pending, and the classic CLI prints deferred notices once. A successful MCP reload remains successful if only the subsequent count read times out.

## How it works

`reply_scoped` records pending inventory holds in a ContextVar; the control handler's return schedules release on a later loop pass. Build completion publishes only after the last hold drops, with a published guard preventing duplicates. Failure composes a normal empty tools/skills/server shape with `inventory_pending = False`.

The classic gateway connection delivers `session.info` listeners after merging the payload. Startup registers a one-shot listener and then immediately checks current info, covering a frame already applied by the pump. `/reload-mcp` uses the full `session.info` RPC rather than a budgeted resume snapshot for the count. Its reload and count-read error boundaries are separate: reload failure still reports failure; a later `GatewayClientError` reports the localized “MCP reloaded — tool count unknown” message and does not invent zero tools.

## Code

- `gateway/session_controls.py`
- `gateway/session_local_inventory.py`
- `hermes_cli/cli_info_mixin.py`
- `hermes_cli/cli_init_mixin.py`
- `hermes_cli/gateway_connection.py`
- `locales/*.yaml`: adds `cli.reload_mcp.agent_updated_count_unknown` in the locale files carried by patch 3831.

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/gateway/test_session_create_inventory_budget.py` (patch 3798).
- `tests/hermes_cli/test_cli_gateway_inventory_pending.py` (patch 3798).
- `tests/hermes_cli/test_gateway_reload_mcp.py` (patches 3798, 3831).

- **3798:** Gateway tests pin reply-before-late-info ordering, a settled empty inventory on early/late build failure and pending-map cleanup. Classic CLI tests pin one-time deferred notices/warnings and no listener for settled replies. MCP reload uses session.info and adopts its full tool inventory.
- **3831:** A successful reload followed by session.info timeout prints success with unknown count, preserves unknown inventory and makes no false available-tools claim; reload.mcp timeout still prints failure and performs no count read.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

This relies on the transport queuing the reply in the handler's return pass; the tests pin that ordering. The full inventory read can exceed the RPC timeout under load. Unknown tool count is not a failed reload, but neither is it a verified inventory. Deferred notices are printed once, not a second full banner.

## Plugin?

No. RPC transport ordering, session snapshots and classic CLI state/listeners cross core boundaries.
