# core-update

Same-checkout build progress/failure remains visible while the installed Desktop
bundle is old. Ready offers clear once its installed stamp matches the target or
has equivalent build inputs. Unknown installed identity never offers a same-HEAD
restart. There is no local-build action or activity override.

Status-bar chip for a Hermes core update that is built in the background and applied the next
time Desktop quits.

- **Building update…** while an updater packs the next version off to the side.
- **Update ready — restart to apply** once a prebuilt app newer than the running checkout is
  waiting. Clicking it starts the plugin-owned, attested apply-on-quit waiter and then quits
  Desktop. The waiter runs the update and reopens Desktop. Restart is disabled if the
  installed reference updater is missing or unsafe (not a regular file of this user, or
  group/other writable).
- Hidden otherwise (no state file, the build is what already runs, or it failed).

## Contract

The updater writes `$HERMES_HOME/cache/core-update/state.json`:

```json
{"status": "building|ready|failed", "sha": "<commit>", "app_path": "<prebuilt Hermes.app>",
 "built_at": "<iso>", "started_at": "<iso>", "pid": 123, "error": null}
```

State is advisory, never executable configuration: legacy `waiter_cmd` is ignored,
including when absent or malformed. `state.json` must be a regular, non-symlink
file owned by the current uid with no group/other write permission (0600 recommended).
Restart checks metadata on the opened fd and parses exactly one snapshot.

`waiter.py` is launched with `/usr/bin/python3 -I`. It reads only the fixed
`~/.hermes/sync-runner/hermes-sync`, requires a regular file of the current uid without
group/other write, and executes the bytes read from that fd with the fixed `core-waiter`
argument. No content hash is pinned: the updater changes often, and launchd already runs the
same file. Threat model: state.json (and anything that can only write it) cannot select code
or argv; a same-uid writer of the installed runner is out of scope. The updater may re-exec
itself on interpreters lacking its dependencies, which reopens the path. Restart still quits
Desktop if the spawned waiter later fails these checks; the sync-armed waiter normally covers
that. This replaces the old arbitrary updater-argv extension point. This plugin does not change
the updater's drain logic.

`waiter.json` next to it (`{"pid", "state"}`) is shown in `/status` when present. The plugin never
edits the checkout. The reference updater is a sync job that keeps a fork checkout current. It
packs `origin/main` in a detached worktree and passes the bundle to `hermes update` through
`HERMES_DESKTOP_PREBUILT_APP`. That env var needs the fork commit "swap in a prebuilt desktop app
named by HERMES_DESKTOP_PREBUILT_APP" (see `core/` in this repo).

API: `GET /api/plugins/core-update/status`, `POST /api/plugins/core-update/restart`, `GET /ping`.

The Desktop chip always calls this machine's gateway (the `local` connection), even when another gateway (e.g.
hermes-hub) is active: the update is for the Desktop on this machine and `/restart` quits it with osascript on the
backend's host. It reads `/status` every 30 s only while the window is visible, and once when it is shown again. That read is
`passive`, so it never starts or installs a local backend that is not running; the Restart click may.

## Tests

```sh
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/core-update/tests
node --test plugins/core-update/tests/test_chip.mjs desktop-plugins/core-update/test/*.test.mjs   # Node >= 22
```
