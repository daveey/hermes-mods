# Discover custom-label launchd gateway supervisors and hand back ownership

Patches: 1154.

## Behavior

- When no canonical job exists, a loaded, enabled KeepAlive LaunchAgent serving the exact resolved `HERMES_HOME` through `gateway run` is the home’s service, even under a custom label. A launcher’s `--home` must also match.
- Ensure uses plain `launchctl kickstart` for a stopped discovered job, never `-k` and never an unmanaged gateway beside it. A running custom job is awaited, not restarted.
- `gateway restart --detach` hands the home to a discovered service instead of spawning an unmanaged replacement. Without any service, unmanaged replacement remains available.
- A `--external-supervisor` start refused by a live owner identifying as `manual` writes that owner’s planned-stop marker and still exits 1. The supervisor’s next restart can take over after graceful drain; supervised, Desktop and silent owners are not asked to stop.

## How it works

Fallback discovery scans `~/Library/LaunchAgents`, validates the plist home and argv, and confirms the job is loaded through launchctl. Home comparison resolves symlinks but does not merge separate profile homes. Detached replacement reuses service discovery. Gateway bootstrap requests manual-owner handback through its existing planned-stop mechanism.

## Code

- `hermes_cli/gateway_runtime_service.py`
- `hermes_cli/gateway_runtime_start.py`
- `gateway/run_bootstrap.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_gateway_runtime_custom_launchd.py`
- `tests/hermes_cli/test_gateway_start_refused.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

The discovery, ensure, detached-restart and handback matrix tests stub launchctl/plists and owner responses. They run on Linux but do not exercise a real macOS LaunchAgent. Disabled, unloaded, non-KeepAlive, wrong-home, wrong-launcher-home and non-gateway jobs are rejected. Handback is not forced termination.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
