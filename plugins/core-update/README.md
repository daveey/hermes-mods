# core-update

Same-checkout build progress/failure remains visible while the installed Desktop
bundle is old. Ready offers clear once its installed stamp matches the target or
has equivalent build inputs. Unknown installed identity never offers a same-HEAD
restart. There is no local-build action or activity override.

Status-bar chip for a Hermes core update that is built in the background and applied the next
time Desktop quits.

- **Building update…** while an updater packs the next version off to the side.
- **Update ready — restart to apply** once a prebuilt app newer than the running checkout is
  waiting. Clicking it starts the updater's apply-on-quit waiter (`waiter_cmd`) and then quits
  Desktop. The waiter runs the update and reopens Desktop.
- Hidden otherwise (no state file, the build is what already runs, or it failed).

## Contract

The updater writes `$HERMES_HOME/cache/core-update/state.json`:

```json
{"status": "building|ready|failed", "sha": "<commit>", "app_path": "<prebuilt Hermes.app>",
 "built_at": "<iso>", "started_at": "<iso>", "pid": 123, "error": null,
 "waiter_cmd": ["<argv that waits for Desktop to quit, then applies the update>"]}
```

`waiter.json` next to it (`{"pid", "state"}`) is shown in `/status` when present. The plugin never
edits the checkout. The reference updater is a sync job that keeps a fork checkout current. It
packs `origin/main` in a detached worktree and passes the bundle to `hermes update` through
`HERMES_DESKTOP_PREBUILT_APP`. That env var needs the fork commit "swap in a prebuilt desktop app
named by HERMES_DESKTOP_PREBUILT_APP" (see `core/` in this repo).

API: `GET /api/plugins/core-update/status`, `POST /api/plugins/core-update/restart`, `GET /ping`.

The Desktop chip always calls this machine's gateway (the `local` connection), even when another gateway (e.g.
hermes-hub) is active: the update is for the Desktop on this machine and `/restart` quits it with osascript on the
backend's host. It reads `/status` every 30 s only while the window is visible, and once when it is shown again.

## Tests

```sh
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/core-update/tests
node --test plugins/core-update/tests/test_chip.mjs desktop-plugins/core-update/test/*.test.mjs   # Node >= 22
```
