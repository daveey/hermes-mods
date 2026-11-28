# core-update

Local version, plain-English update summary, and blocker controls for the titlebar
[Hub panel](../../desktop-plugins/hub-status/). There is no footer update chip or
version pill.

The Hub shows an up-arrow for a ready/building update with pending changes. Its
**What's in the update** section replaces raw git hashes and truncated commit
subjects with:

- A plain-English status line (`Up to date`, `N changes ready · no app restart needed`,
  or `restarts Desktop when ready`), with zero git SHAs in user-facing copy.
- A bulleted summary of what to expect from the update: paused chats that will
  resume automatically, linked sessions/tasks unblocked by the incoming changes,
  and grouped plain-English fix bullets (upgraded asynchronously via
  `hermes-pool-llm` and cached under `cache/core-update/summaries/`, with an
  instant deterministic fallback so `GET /version` never blocks).
- When an update is waiting or blocked, a **Waiting on** list naming the actual
  scheduled cron jobs, chat turns, card workers, or batch window holding it up
  (suppressing duplicate daemon `gateway ... pid=...` census strings), with
  per-item **Stop** / **Skip wait** buttons and a **Stop all** button when
  multiple cancellable runs are holding the update.

**Update now** works even without a pending prebuild. It writes the standard
30-minute `hermes_cli.core_update_force.write_force_request(..., by="hub panel")`
intent and invokes the installed attested updater's `core` command to fetch latest.
Busy or unknown running-turn counts require confirmation in the panel; cancel
writes nothing. All version/update/blocker requests target the Desktop machine's
**local** backend, independently of the selected gateway; hub history still targets
the hub.

The detached monitor publishes operation state/errors to
`cache/core-update/hub-update.json` and updater output to `hub-update.log`. Standard
core progress and live waiter state provide actual deferral reasons. A backend-only
update never quits Desktop. Only a ready, restart-capable bundle serving latest
`origin/main` whose build inputs differ from the installed bundle triggers the
existing apply-on-quit waiter and quit/reopen flow. Unknown bundle identity does
not authorize a quit. Concurrent windows share one installation-level monitor lock.
After reopening, the view reconciles the monitor's swap receipt against checkout
and installed bundle identity. A dead monitor without a completed swap reports
a retryable deferral, not endless Updating. Completed/error receipts expire, so
historical results do not mask current status.

## API

Prefix `/api/plugins/core-update`:

- `GET /ping`: `{ "ok": true, "api": 3 }`.
- `GET /version`: human status, summary bullets, structured blockers, raw changes, turn count, force intent and progress.
- `POST /update`: record explicit full-force intent and start the detached updater.
- `POST /blocker`: stop a cancellable blocker (`kind: "cron"` writes per-execution `.cancel` markers; automated `kind: "turn"` records a CAS `session_turn_end_requests` row) or `action: "stop_all"`. When a running Desktop backend predates `api: 3`, the Hub panel falls back to core's `POST /api/cron/jobs/{id}/cancel`.
- `GET /status`, `POST /restart`: retained legacy endpoints. `/restart` writes the
  dedicated quit-only `restart-request`, not a full-force request.

The installation root is `$HERMES_HOME`, or its grandparent for `profiles/<name>`.
The updater is always `~/.hermes/sync-runner/hermes-sync`, opened and checked as a
regular current-uid file without group/other write permission. No browser/state
value chooses code or arguments. `waiter.py --update` runs the fixed `core` command;
without that flag it runs fixed `core-waiter`. The plugin never edits the checkout
itself, and state `waiter_cmd` is ignored.

`state.json` is advisory and must be a regular, non-symlink, current-uid file with
no group/other write permission. Ready/same-HEAD offers follow installed Desktop
stamps/build inputs, not just checkout equality. Stale/dead waiter records are not
presented as active deferrals. The reference updater owns draining and safety gates.

## Tests

- `plugins/core-update/tests/test_version.py` (`GET /version`, `POST /update`, monitor lifecycle, Desktop reopen store contract)
- `plugins/core-update/tests/test_human_panel.py` (session/task unblock summary, `hermes-pool-llm` fallback/cache, live cron/turn blocker resolution, `Stop` / `Stop all` safety)
- `plugins/core-update/tests/test_api.py` (ASGI `/ping`, `/status`, `/restart`, `/update`, `/blocker`)
- `desktop-plugins/hub-status/test/chip.test.mjs` (Hub popover `VersionSection` zero-hash rendering, summary bullets, blocker `Stop` / `Stop all` + `api: 2` fallback)

```sh
scripts/test-all -k core-update
node --test desktop-plugins/hub-status/test/chip.test.mjs
```
