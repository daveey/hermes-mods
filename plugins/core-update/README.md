# core-update

Status-bar chip for a Hermes core update that is built in the background and applied the next
time Desktop quits.

- **Building update…** while an updater packs the next version off to the side.
- **Update needs a build: Build now (~26 min)** when a peer is unavailable and local work needs consent.
  Clicking starts the updater's `local_build_cmd`; it does not quit Desktop. Storage, locking and host
  safety gates still apply. The duration is an estimate, not a completion deadline.
- **Update ready — restart to apply** once a prebuilt app newer than the running checkout,
  or a build for an outstanding Desktop swap at the current checkout, is waiting. Clicking
  starts the updater's apply-on-quit waiter (`waiter_cmd`) and then quits
  Desktop. The waiter runs the update and reopens Desktop.
- Hidden otherwise (no state file, the build is what already runs, or it failed).

## Contract

The updater writes `$HERMES_HOME/cache/core-update/state.json`:

```json
{"status": "building|ready|failed|needs-local-build", "sha": "<commit>", "app_path": "<prebuilt Hermes.app>",
 "built_at": "<iso>", "started_at": "<iso>", "pid": 123, "error": null,
 "waiter_cmd": ["<argv that waits for Desktop to quit, then applies the update>"]}
```

`waiter.json` next to it (`{"pid", "state"}`) is shown in `/status` when present. The plugin never
edits the checkout. The reference updater is a sync job that keeps a fork checkout current. It
packs `origin/main` in a detached worktree and passes the bundle to `hermes update` through
`HERMES_DESKTOP_PREBUILT_APP`. That env var needs the fork commit "swap in a prebuilt desktop app
named by HERMES_DESKTOP_PREBUILT_APP" (see `core/` in this repo).

For `needs-local-build`, supply `peer_blocked_reason` and `local_build_cmd` as
`["<python>", "<sync-script>", "core-prebuild", "<full sha>", "--local-now"]`.
It is shown even when the checkout already has the target (the installed bundle may not).
Older updaters without this command cannot offer the build action.

Checkout HEAD can advance before Desktop's bundle is swapped. At equal HEAD, `/status`
reads `apps/desktop/release/*/Hermes.app/Contents/Resources/install-stamp.json` from the
backend checkout, never the staged app. `desktop_swap_pending` remains true through
building, failure and ready while installed build inputs differ. Once the installed
commit equals the target (or Git proves all Desktop build inputs identical), the offer
clears. Missing/malformed installed identity leaves this field null and does not authorize
a same-HEAD restart; build/failure diagnostics remain available. No extra state writer
or stale success marker is needed. Tests use real Git commits and installed-stamp files
for both successful and failed build lifecycles, already-installed/equivalent controls,
and missing/malformed identity.

API: `GET /api/plugins/core-update/status`, `POST /api/plugins/core-update/restart`,
`POST /api/plugins/core-update/build` (explicit local-build consent), `GET /ping`.

## Tests

```sh
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/core-update/tests
node --test plugins/core-update/tests/test_chip.mjs   # Node >= 22
```
