# Disk plugin refresh exclusion during queued scans

Patch **2053**. Follow-up to [desktop plugin loader refresh](desktop-plugin-loader-refresh.md).

## Behavior

A Desktop disk-plugin refresh tick skips work while a discovery scan is in flight rather than racing the scan for ownership of the disk-plugin registry.

## How it works

`refreshChangedDiskPlugins` checks `scanInFlight` instead of the superseded `scanning` flag. The existing guards for missing Desktop API, another refresh and an empty disk registry remain. A skipped refresh returns an empty list; discovery can itself load the changed bytes, and later refresh ticks can recheck.

## Code

`apps/desktop/src/contrib/runtime-loader.ts`.

## Tests

**Not rerun this pass.** `apps/desktop/src/contrib/runtime-loader-refresh.test.ts`, case `a refresh tick while a scan is in flight is skipped, never racing the scan`, starts manual `discoverRuntimePlugins`, holds its root lookup promise, changes plugin source, invokes `refreshChangedDiskPlugins`, and verifies no extra file read or activation before release. Releasing discovery activates the new version once. This is a mocked Desktop API Vitest path, not live Desktop deployment proof.

## Applying

Apply 2053 via [the published replay](README.md) after the upstream queued-scan integration that defines `scanInFlight`.

## Gotchas

Skipping a tick is intentional and is not an unload. The patch preserves exclusion against the newer scanner state rather than adding a second refresh queue.

## Plugin eligibility

Not plugin-only: the core runtime loader owns scan/refresh serialization; a plugin cannot safely enforce this around its own loader.
