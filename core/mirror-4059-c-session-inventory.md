# Session-scoped inventory waits and reply publication ordering

Mirrored entries: 3845, 3880.

## Behavior

The classic CLI never prints or adopts a session’s late startup inventory after switching away from that session. Settling twice prints notices only once. Re-adopting the same snapshot does not count as a switch.

Tests also pin the gateway rule that a shared deferred inventory build publishes its settled info only after every pending create reply has released its hold.

## How it works

`GatewaySessionStore.set_current` becomes the central switch point: it runs and clears switch listeners before changing snapshot identity. Snapshot restore and session end use that method. `_adopt_inventory` installs paired settle/switch callbacks, with a nonblocking lock giving only one callback ownership of cleanup. After cleanup, the settle path independently verifies that the original snapshot is still current before merging info, printing notices or adopting toolsets.

The follow-up tests deliberately retain a copied callback and deliver duplicate settled frames to exercise the lock rather than only listener removal. A concurrent gateway fixture holds two replies around one inventory build, then releases them separately to establish publication order.

## Code

- `hermes_cli/cli_init_mixin.py`
- `hermes_cli/cli_snapshot.py`
- `hermes_cli/gateway_session_store.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_cli_gateway_inventory_pending.py`: Checks no notices/toolset adoption from a left session, cleanup on switch/end, same-snapshot adoption, identity fallback when hooks are bypassed, and one print batch despite two callbacks reaching the settle path.
- `tests/hermes_cli/test_gateway_snapshot_restore_guards.py`: Checks the switch listener is not fired by the preliminary rejoin and is fired by the actual snapshot restore.
- `tests/gateway/test_session_create_inventory_budget.py`: Pins two pending-reply holds on one build, no settled publication while either hold remains, and one final info frame after both replies. Uses the inventory budget plus scheduling allowance rather than a brittle fixed reply-time limit.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Snapshot identity, not merely session-name equality, fences the CLI wait. Removing a callback alone does not prevent an already copied callback from running; keep the lock and current-snapshot check. These fixtures do not establish a live interactive CLI or Desktop deployment.

## Plugin?

No. Store lifecycle and gateway reply-hold ordering are core protocol responsibilities.
