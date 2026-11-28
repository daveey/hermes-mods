# core-update

Local version and update backend for the titlebar [Hub panel](../../desktop-plugins/hub-status/).
There is no footer update chip or version pill.

The Hub shows an up-arrow for a ready/building update with pending commits. Its
**Hermes version** section shows the running backend revision, installed Desktop
build, applied checkout, target and newest-first `running..target` commit subjects/times (collapsed
after 15). Version reads never fetch or edit Git.

**Update now** works even without a pending prebuild. It writes the standard
30-minute `hermes_cli.core_update_force.write_force_request(..., by="hub panel")`
intent and invokes the installed ownership/permission-checked updater's `core` command to fetch latest.
These checks are not a source-review attestation.
Busy or unknown running-turn counts require confirmation in the panel; cancel
writes nothing. All version/update requests target the Desktop machine's **local**
backend, independently of the selected gateway; hub history still targets the hub.

The detached monitor publishes operation state/errors to
`cache/core-update/hub-update.json` and updater output to `hub-update.log`. Standard
core progress and live waiter state provide actual deferral reasons. A backend-only
update never quits Desktop. Only a ready, restart-capable bundle serving latest
`origin/main` whose build inputs differ from the installed bundle triggers the
existing apply-on-quit waiter and quit/reopen flow. Unknown bundle identity does
not authorize a quit. Concurrent windows share one installation-level monitor lock.
After reopening, the view reconciles the monitor's swap receipt against checkout
and installed bundle identity. A dead monitor without a completed swap reports
a retryable deferral, not endless Updating. The running backend keeps its boot
SHA until its own process restarts; Applied checkout names the installed source.
Completed/error receipts expire, so historical results do not mask current status.

## API

Prefix `/api/plugins/core-update`:

- `GET /ping`: `{ "ok": true, "api": 2 }`.
- `GET /version`: version/status, commit subjects, turn count, force intent and progress.
- `POST /update`: record explicit full-force intent and start the detached updater.
- `GET /status`, `POST /restart`: retained legacy endpoints. `/restart` writes the
  dedicated quit-only `restart-request`, not a full-force request.

The installation root is `$HERMES_HOME`, or its grandparent for `profiles/<name>`.
The updater is always `~/.hermes/sync-runner/hermes-sync`, opened and checked as a
regular current-uid file without group/other write permission. Parent components
are checked through directory descriptors; symlink/foreign/writable parents are
refused (root-owned system ancestors are allowed). The plugin supplies the opened
snapshot to the runner. Its isolated child bootstrap must compare that snapshot's
SHA256 before compiling a later opened runner: a concurrent runner replacement is
a refusal, not permission to execute newer bytes. Restart bootstraps the backend's
managed parser runtime instead of starting the system Python.

This is content continuity, **not source approval or enrollment**. The companion
state runner change is required for child/reexec continuity. Installed runner parity,
full deciding runner behavior, exact managed-core receipt controls, live service
configuration and the Hub renderer retain their own independent review/live gates.
No browser/state
value chooses code or arguments. `waiter.py --update` runs the fixed `core` command;
without that flag it runs fixed `core-waiter`. The plugin never edits the checkout
itself, and state `waiter_cmd` is ignored.

`state.json` is advisory and must be a regular, non-symlink, current-uid file with
no group/other write permission. Ready/same-HEAD offers follow installed Desktop
stamps/build inputs, not just checkout equality. Stale/dead waiter records are not
presented as active deferrals. The reference updater owns draining and safety gates.

## Tests

```sh
scripts/test-all -k core-update
node --test desktop-plugins/hub-status/test/chip.test.mjs
```
