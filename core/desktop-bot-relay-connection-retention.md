# Bot relay retains one route per connection

Patch **1661** (`13eb835894cb8019c0c355ec391ae3831034e555`).

## Behavior

Desktop pins, roster-syncs and drains one representative profile route per eligible connection, instead of retaining every profile backend. It prefers the `default` profile; if absent, it chooses the first listed route. Delivery still targets the envelope's exact destination profile on demand.

## How it works

`relayRepresentativeRoutes()` groups eligible routes by `connectionId`, replacing an earlier named-profile representative when a default route appears. `relayConnections()` supplies those representatives to the existing retention, roster and drain paths. The gateway relay methods resolve profile homes to one install root: roster, outbox and replies are shared, so a separate permanent pin for each profile is unnecessary. This removes the relay's reason for keeping all isolated profile backends alive; it does not force an active backend to exit.

## Code

`apps/desktop/src/plugins/hermes-bots/relay.ts` adds the representative selector. Tests also exercise existing `tui_gateway/methods_bot_relay.py` behavior; that gateway implementation is not changed by this patch.

## Tests

In `apps/desktop/src/plugins/hermes-bots/relay.test.ts`:

- `pins, roster-syncs and drains a many-profile remote connection through ONE default route`
- `falls back to the first listed route when a connection exposes no default profile`
- `drains a connection once through its default route and delivers on the exact target profile`

Existing per-profile retention assertions are adjusted to the per-connection contract. `tests/tui_gateway/test_bot_relay_methods.py::test_every_profile_of_one_install_shares_one_relay_mailbox` checks default-to-named, named-to-default and named-to-named gateway mailbox access.

These source tests were not rerun in this mirror. Desktop RPC is mocked. The gateway test writes an install-root envelope directly; it does **not** prove where a named profile's `message_agent` producer writes. Actual backend idle-exit and memory savings are not exercised by these tests.

## Applying

Apply 1661 in the [ordered mirror replay](README.md), with the existing Desktop relay and gateway relay doors. Rebuild Desktop through the source repository's supported build workflow; copying this document does not deploy it.

## Gotchas

The optimization relies on install-wide relay storage. Keep target-profile delivery distinct from the representative route used for shared mailbox operations. Other socket consumers can still retain a profile backend. No plugin configuration is added.
