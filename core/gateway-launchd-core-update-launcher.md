# Gateway plist runs under the core-update launcher

Patch **0539** (source `4148caceab`).

## Behavior

A fenced core updater can refuse to update until every launchd gateway runs under a stable launcher
outside the checkout. The launcher holds a shared lock on `<home>/core-update/exec.lock` for its child's
life and drains the child with one SIGUSR1 when an update intent appears. The core's gateway plist
template knew nothing of it, so a hand-repointed plist was reverted by `refresh_launchd_plist_if_needed`
at the next `hermes update` or `hermes gateway start/restart`.

Now, when `<installation root>/bin/hermes-gateway-launcher` is installed and executable,
`generate_launchd_plist()` emits it. A profile home maps to its root's `bin/`.

| Host | ProgramArguments shell command |
|---|---|
| launcher installed | `exec <launcher> --home <home> -- <stderr_timestamp command> >> gateway.log 2>> gateway.error.log` |
| no launcher (every other install) | unchanged: `exec <stderr_timestamp command> >> …` |

The launcher goes inside the job's `/bin/sh -c exec …` string, not around the job's first executable.
The signed-bundle shim (Hermes Python.app) and the osascript wrapper forward no SIGUSR1 and spawn `sh`
without the launcher's lock fd. Wrapped from outside, a drain would kill the shim and orphan a gateway
that holds no lock, so the updater could swap the checkout under a live gateway. `stderr_timestamp`
forwards SIGUSR1 to the gateway and exits with its status (75 after a drain).

`gateway.status` treats the launcher's own process line like the osascript wrapper's: it carries the
gateway argv as data and is never taken for the gateway, so stop/status/update never signal or count it.

## How it works

- `hermes_cli/gateway_launchd.py`: `GATEWAY_LAUNCHER_NAME`, `gateway_launcher_path(home)`,
  `gateway_launcher_prefix(home)`. `generate_launchd_plist` prepends the prefix to the
  `stderr_timestamp` command before `launchd_program_arguments` wraps it.
- `gateway/status.py` `_gateway_command_subcommand`: `hermes-gateway-launcher` as the first or second
  token returns None, next to the osascript check.

## Tests

- `tests/hermes_cli/test_gateway_launchd_core_update_launcher.py`:
  - signed shim: launcher prefix sits inside the `exec` string, and the launcher line is not a gateway;
  - osascript form gets the same prefix;
  - a profile home uses the root's launcher with its own `--home`;
  - no launcher → unchanged;
  - a non-executable launcher is ignored.
- `tests/gateway/test_status_core_update_launcher.py`: the launcher line (direct or under an
  interpreter) is not a gateway; the gateway child's own line still is.
- The live chain (shim → sh → launcher → real `stderr_timestamp` → gateway: lock held while it runs,
  SIGUSR1 reaches the gateway, exit 75) is tested on the updater side, next to the launcher.

## Applying

`git am -3` after the signed-bundle launchd wrapper patch (`launchd_program_arguments(..., responsible=)`).
It only matters where something installs `~/.hermes/bin/hermes-gateway-launcher`.

## Gotchas

- The updater's gate must accept the launcher inside the shell string (`exec <launcher> --home <home> -- …`),
  not only as its own argv element.
- `stderr_timestamp` spawns the gateway with `close_fds`, so the gateway itself does not hold the lock.
  If `stderr_timestamp` is SIGKILLed alone, the gateway is orphaned without the lock. Only the updater's
  busy recheck covers that case.
- Every launcher writes `core-update/launchers/<pid>.json` (not one shared `launcher.json`: an orphan
  overwrote the live job's attestation). Run one long-lived launcher per home; a dead pid's file is reaped.
