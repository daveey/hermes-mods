# Supervised gateway dashboard READY barrier

Mirror batch 6; ordinals 4057.

## Behavior

Supervised gateways begin dashboard mount after listener startup, overlap it with adapter/cron startup and await a bounded mount barrier before READY. Idle-exit gateways stay lazy. Explicit ensure/attach defaults and Desktop’s client deadline cover the longer cold-start envelope.

## How it works

Bootstrap starts warmup only outside idle-exit mode and awaits it on normal and early-verdict paths. asyncio.shield prevents the 30-second barrier timeout from cancelling a mount; timeout/failure warns and permits READY, with first-request retry after failure. CLI ensure/attach defaults become 330 seconds while explicit timeout overrides survive. Desktop’s protocol deadline matches 330 seconds and retains separate output/interpreter grace.

## Code

Source paths touched by this feature group (including regression fixtures):

- `apps/desktop/electron/local-gateway-deadline.test.ts`
- `apps/desktop/electron/local-gateway.ts`
- `gateway/run_api.py`
- `gateway/run_bootstrap.py`
- `hermes_cli/subcommands/gateway.py`
- `tests/gateway/test_bootstrap_dashboard_ready.py`

Mail entries in the ordered series:

- 4057: [`623e45e1b01540444eaaeb961d234d2179d5e7fd`](patches/4057-daveey-Wait-for-supervised-dashboard-mount-before-gateway-READY.patch)

## Tests

- `tests/gateway/test_bootstrap_dashboard_ready.py`: slow mount blocks READY until released; timed-out mount remains pending and can finish after READY; failed mount warns then a real ASGI request retries. Tests cover idle-exit absence of dashboard import/task, early verdict completion, overlap with adapter startup, missing/failed warmup handling, and CLI default/explicit deadline parsing.
- `apps/desktop/electron/local-gateway-deadline.test.ts`: fake-timer coverage accepts a protocol verdict arriving at the 330-second default rather than killing its client too early.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

READY is not an unconditional dashboard-mount guarantee after timeout/failure. Default explicit starts can now wait longer; callers may supply a shorter timeout. Native/live restart and display verification are separately owned, not proven by these tests.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
