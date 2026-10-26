# Fork updates and relaunch: keeping your own fork of hermes-agent

These four patches make it practical to run Hermes from **your own fork** of `NousResearch/hermes-agent`, with
your changes committed on `main`:

- `hermes update` merges upstream into the fork instead of giving up as soon as you have a commit of your own.
- A headless update (launchd, cron, a sync script) reopens the Desktop app it had to close.
- `hermes serve --stop` no longer kills the backend under your open Desktop window.
- In fleet mode, switching to "This device" no longer asks for confirmation.

0007 is the one that matters for keeping a fork. The other three remove friction once updates run unattended
and you work in Desktop.

| patch | what it does | setting (default) |
|---|---|---|
| 0007 | `hermes update` merges `upstream/main` into a fork that has its own commits | `updates.fork_merge_upstream` (`false`) |
| 0011 | reopens the Desktop app that a headless update stopped for the bundle swap | `desktop.relaunch_after_rebuild` (`false`) |
| 0012 | `hermes serve --stop` / `hermes dashboard --stop` skip Desktop-owned backends | none, always on |
| 0016 | no "Switch to This device?" dialog when the local runtime is installed | none, always on |
| 0066 | `hermes update` swaps in a desktop app you prebuilt from the new HEAD instead of packing it | env `HERMES_DESKTOP_PREBUILT_APP` (unset) |

## The fork workflow

Your Hermes checkout (`~/.hermes/hermes-agent` by default) is a clone of **your fork**. Your mods are ordinary
commits on `main`. `hermes update` keeps `main` current by merging upstream into it, so you never rebase and
never re-apply patches.

### Remotes

| remote | URL | role |
|---|---|---|
| `origin` | `git@github.com:<you>/hermes-agent.git` | `hermes update` fast-forwards from it, then pushes the merged result back |
| `upstream` | `https://github.com/NousResearch/hermes-agent.git` | `hermes update` fetches `upstream/main` and merges it in |

`hermes update` treats the checkout as a fork whenever `origin` is not the official repo (`_is_fork` in
`hermes_cli/update_cmd_git.py`). If `upstream` is missing, an interactive run offers to add it. A run with
`--yes` or without a TTY only prints the command, so set both remotes up once:

```sh
cd ~/.hermes/hermes-agent
git remote rename origin upstream                      # only if you cloned NousResearch directly
git remote add origin git@github.com:<you>/hermes-agent.git
git fetch upstream && git push -u origin main
```

### Day-to-day

1. Edit and test in the checkout, or in a `git worktree` of it.
2. Commit to **`main`**. The upstream sync only runs when the checkout is on `main`.
3. Put a **handle tag** in the subject, using the upstream conventional-commit style:
   - `fix(serve): --stop leaves Hermes Desktop-owned backends running [<you>]`
   - for personal-only commits, put the tag first: `[<you>] re-exec into a signed Python.app`

   Upstream never uses these tags, which helps in three places:
   - Listing your mods: `git log --oneline --no-merges upstream/main..main`. Filter with
     `--grep '\[<you>\]'`.
   - Conflicts: you can tell at a glance which side of a hunk is yours.
   - Export: `git format-patch --no-merges upstream/main..main -o <dir>`. This is how the files in
     `core/patches/` were produced.
4. `git push origin main` so your other machines get the change.
5. Run `hermes update` (or let it run on a schedule). It merges upstream and pushes the merge to `origin`.

Keep each mod to one focused commit, with its tests in the same commit. Conflicts stay small, and if upstream
later ships the same fix you can `git revert` yours.

## Behavior

### 0007: `updates.fork_merge_upstream`

```yaml
updates:
  fork_merge_upstream: true   # default false = stock behaviour
```

The update proceeds in this order (`hermes_cli/update_cmd.py`):

1. Fast-forward `main` from `origin/main`.
2. Run `_sync_with_upstream_if_needed`: fetch `upstream/main`, then count commits in each direction between
   `origin/main` and `upstream/main`.

| fork vs upstream | stock | with the flag |
|---|---|---|
| equal | "Fork is up to date with upstream" | same |
| behind only | fast-forward to upstream | same |
| ahead only | "Skipping upstream sync to preserve your changes" | "Fork is up to date with upstream (N fork commit(s) on top)" |
| ahead **and** behind | skip; tells you to `git pull upstream main` | **merge upstream into the fork**, then push to `origin` |

- **The merge never touches your working tree.** It is computed off-tree, and the live checkout only
  fast-forwards to the result. Upstream pulls go through the same guarded path: custody marker, pre-move syntax
  check, and rollback on failure.
- **The merge commit:** parents are `HEAD` and `upstream/main`; the message is
  `Merge upstream/main (<sha10>) into fork`.
- **A conflict moves nothing.** HEAD, the working tree and `origin/main` stay where they were, and the update
  prints:

  ```
  ✗ Upstream does not merge cleanly into your fork — nothing changed.
    Conflicting paths: <paths>
    Resolve by hand: git merge upstream/main, fix, commit, git push origin main
  ```

  An unattended update keeps running your current version until you resolve the conflict once by hand.
- **Push:** `git push origin main --force-with-lease`. It runs only after the update has validated, and only if
  HEAD contains `upstream/main` and descends from `origin/main`. Without push access you get a notice; the local
  checkout is still updated.
- **Several machines:** the first machine to update creates the merge and pushes it. The others fast-forward to
  it in step 1. If two machines merge at the same moment, the second push loses its lease and fails harmlessly.
  Fix that machine with `git pull origin main`.
- **Flag off:** stock behaviour. The skip message now also says "(or set updates.fork_merge_upstream: true …)".

### 0011: `desktop.relaunch_after_rebuild`

```yaml
desktop:
  relaunch_after_rebuild: true   # default false = stock: a headless update leaves the app closed
```

An update that rebuilds Desktop swaps the new bundle into `apps/desktop/release/`. It first stops every Desktop
process running from that tree, because a renderer that survives the swap loads stale hashed chunks and crashes.

- **In-app Update button:** quits and reopens the app itself. This patch changes nothing there.
- **Headless update** (`hermes update` from a terminal, launchd, cron, `hermes desktop --build-only`): stock
  just stops the app. With this patch, every stopped Desktop **main** process is reopened from the new bundle
  with its original arguments:
  - macOS: `open <Hermes.app> --args …`
  - Linux: the executable directly
- The prompt prints `✓ Reopened the desktop app that was stopped for the rebuild (…)`.
- Electron helper processes (`--type=…`) are never reopened.
- Identical argument lists open only once; the single-instance lock would just focus a duplicate.
- If the swap fails and the old app is kept, the old app is reopened.
- Windows: no-op.

### 0012: `hermes serve --stop` spares Desktop backends

`--stop` used to kill every `hermes serve` / `hermes dashboard` process for the profile, including the backend
a running Desktop spawned. A common way to hit it: an agent *inside* Desktop runs `--stop` to restart a
launchd-managed serve, and SIGTERMs its own backend, cutting off every live turn. `--stop` now skips a PID when
any of these holds:

- its environment has `HERMES_DESKTOP=1` (anything Desktop spawned, pool backends included)
- it is listed in `HERMES_DESKTOP_CHILD_PID`
- a valid `desktop-ssh/<id>/backend.lock.json` claims it (an SSH-attached Desktop)

It prints `Leaving N Hermes Desktop backend(s) running (PID …); quit the app to stop them.` If only Desktop
backends were found, it kills nothing and exits 0. The backend cleanup after an update is unchanged.

### 0016: no "Switch to This device?" confirm

In fleet mode (several gateways in the profile rail), clicking **This device** used to open a confirmation
dialog whenever the switch would start a fresh session (default profile, no profile, or `replaceCenter`). Now
an installed local runtime switches straight through. A dialog appears only when the runtime still needs
installing (`bootstrapNeeded`). That is the install prompt: same "Switch to This device?" title, with
install copy. Cancelling it still aborts the switch.

## How it works

- **0007, `hermes_cli/update_cmd_git.py`:**
  - `_fork_merges_upstream()` reads the flag.
  - `_synthesize_upstream_merge()` runs `git merge-tree --write-tree HEAD <upstream>` to get a merged tree
    without a checkout. A non-zero exit means a conflict; the conflicting paths are listed with
    `--name-only`.
  - `git commit-tree <tree> -p HEAD -p <upstream>` makes the merge commit. That commit becomes `target`.
  - `_sync_with_upstream_if_needed()` already fast-forwarded to `upstream`. Now it fast-forwards to `target`
    instead, under the caller's `checkout_move(target)` context.
  - `_push_synced_fork()` used to push only when `HEAD == upstream/main`. It now also accepts a HEAD that
    descends from upstream, when the flag is on.
  - Callers in `hermes_cli/update_cmd.py` (the origin-pull path and the `commit_count == 0` path) are unchanged.
- **0011, `hermes_cli/main_desktop.py`:**
  - `_swap_staged_desktop_app()` snapshots `_desktop_main_launches(desktop_dir)` (psutil: exe under
    `release/`, no `--type=` arg) before `_stop_desktop_processes_locking_build`.
  - It keeps the argv of the PIDs that were actually stopped.
  - It calls `_relaunch_stopped_desktops()` after the swap, on both success and failure.
  - The in-app update quits the app before the swap, and the stop spares ancestor Desktops, so `stopped` is
    empty there and nothing is reopened twice.
  - The default value is in `hermes_cli/config_defaults.py`.
- **0012:**
  - `hermes_cli/dashboard_procs.py`: new `_desktop_owned_backend_pids(pids)`, built from existing helpers
    `_lock_owned_serve_pids`, `_exclude_pids_from_env` and `_pid_environ`.
  - `_kill_stale_dashboard_processes` gains an `exclude_pids` parameter.
  - `hermes_cli/main.py` `_dashboard_lifecycle_flags`: the `--stop` branch computes the Desktop-owned set and
    passes it as `exclude_pids`.
- **0016:** a desktop React change only, in `useLocalDeviceSwitch()` in
  `apps/desktop/src/app/chat/sidebar/local-device-switch.tsx`. No gateway or RPC changes.

## Code

`hermes_cli/update_cmd_git.py` (0007):

```python
def _fork_merges_upstream() -> bool:
    with suppress(Exception):
        from hermes_cli.config import load_config
        return bool((load_config().get("updates") or {}).get("fork_merge_upstream", False))
    return False

def _synthesize_upstream_merge(git_cmd, cwd, upstream) -> Optional[str]:
    head = _git_stdout(git_cmd, ["rev-parse", "HEAD"], cwd)
    if not head:
        return None
    tree_proc = subprocess.run([*git_cmd, "merge-tree", "--write-tree", "--no-messages", head, upstream],
                               cwd=cwd, capture_output=True, text=True)
    tree = (tree_proc.stdout or "").split("\n", 1)[0].strip()
    if tree_proc.returncode != 0 or not tree:
        # list conflicts with `merge-tree --write-tree --name-only`, print them, move nothing
        return None
    msg = f"Merge upstream/main ({upstream[:10]}) into fork"
    return _git_stdout(git_cmd, ["commit-tree", tree, "-p", head, "-p", upstream, "-m", msg], cwd) or None

# in _sync_with_upstream_if_needed, after counting origin_ahead / upstream_ahead:
    target = upstream
    if origin_ahead > 0:
        if upstream_ahead == 0:
            print(f"  ✓ Fork is up to date with upstream ({origin_ahead} fork commit(s) on top)"); return True
        if not _fork_merges_upstream():
            print(...stock skip message...); return True
        merged = _synthesize_upstream_merge(git_cmd, cwd, upstream)
        if merged is None:
            return False
        target = merged
    elif upstream_ahead == 0:
        ...
    # every later use of `upstream` (preflight, ff-merge, landed-HEAD check) uses `target`:
        with (checkout_move or _no_move)(target):
            run_git(git_cmd, ["merge", "--ff-only", target], cwd=cwd, check=True, **_no_prompt_git_kwargs())

# in _push_synced_fork:
    if not head or not upstream or head == origin:
        return
    if head != upstream and not (_fork_merges_upstream() and
                                 _git_ok(git_cmd, ["merge-base", "--is-ancestor", upstream, head], cwd)):
        return
```

`hermes_cli/main_desktop.py` (0011):

```python
# _swap_staged_desktop_app, around the stop:
            launches = _desktop_main_launches(desktop_dir)
            stopped = _stop_desktop_processes_locking_build(desktop_dir, also_posix=True)
            if stopped:
                relaunch = [argv for pid, argv in launches.items() if pid in stopped]
# ... in the except (OSError, ValueError) branch and after the finally: _relaunch_stopped_desktops(desktop_dir, relaunch)

def _desktop_main_launches(desktop_dir) -> dict[int, list[str]]:
    if sys.platform == "win32": return {}
    import psutil
    release_dir = (desktop_dir / "release").resolve()
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        exe, cmdline = proc.info.get("exe"), proc.info.get("cmdline") or []
        if not exe or release_dir not in Path(exe).resolve().parents: continue
        if any(str(a).startswith("--type=") for a in cmdline[1:]): continue   # Electron helper
        launches[int(proc.info["pid"])] = [str(a) for a in cmdline[1:]]

def _relaunch_stopped_desktops(desktop_dir, launches):
    if not launches or sys.platform == "win32" or not _desktop_relaunch_after_rebuild_enabled(): return []
    exe = _desktop_packaged_executable(desktop_dir)
    for args in dedupe(launches):
        if sys.platform == "darwin":
            bundle = exe.parents[2]                      # Hermes.app/Contents/MacOS/Hermes
            cmd = ["/usr/bin/open", str(bundle)] + (["--args", *args] if args else [])
        else:
            cmd = [str(exe), *args]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
```

`hermes_cli/config_defaults.py` (0011): `"relaunch_after_rebuild": False,` in the `desktop` section.

`hermes_cli/dashboard_procs.py` and `hermes_cli/main.py` (0012):

```python
def _desktop_owned_backend_pids(pids) -> set[int]:
    owned = (_lock_owned_serve_pids() | _exclude_pids_from_env()) & set(pids)
    for pid in pids:
        if pid not in owned and ((_pid_environ(pid) or {}).get("HERMES_DESKTOP") == "1"):
            owned.add(pid)
    return owned

# _kill_stale_dashboard_processes(..., exclude_pids: set[int] | None = None):
    exclude = _exclude_pids_from_env() | set(exclude_pids or ())

# main.py, `--stop` branch of _dashboard_lifecycle_flags:
        pids = _find_stale_dashboard_pids(scope_home=own_home)
        desktop = _procs._desktop_owned_backend_pids(pids)
        if desktop:
            print(f"Leaving {len(desktop)} Hermes Desktop backend(s) running (PID ...); quit the app to stop them.")
        if not [p for p in pids if p not in desktop]:
            sys.exit(0)
        result = _procs._kill_stale_dashboard_processes(
            reason="requested via --stop", scope_home=own_home, exclude_pids=desktop)
```

`apps/desktop/src/app/chat/sidebar/local-device-switch.tsx` (0016):

```tsx
-    const freshSession = target.replaceCenter === true || target.profile == null || target.profile === 'default'
-    if (!bootstrapNeeded && !freshSession) {
+    if (!bootstrapNeeded) {
       return true
     }
     return new Promise(resolve => {
       waiter.current = resolve
-      setPrompt({ ...target, kind: bootstrapNeeded ? 'install' : 'switch' })
+      setPrompt({ ...target, kind: 'install' })
     })
```

## Tests

| patch | test file | covers |
|---|---|---|
| 0007 | `tests/hermes_cli/test_update_fork_merge_upstream.py` (new) | Uses real temp git repos. Flag off: the fork is untouched. Flag on: a 2-parent merge with both sides' changes, a clean tree, and the fork pushed. Conflict: nothing moves. |
| 0011 | `tests/hermes_cli/test_desktop_swap_relaunch.py` (new) | Reopen from the new bundle; argv kept; setting off; Desktop not stopped means not reopened; a failed swap reopens the kept app; Electron helpers filtered out. |
| 0012 | `tests/hermes_cli/test_dashboard_lifecycle_flags.py` (extended) | `TestDashboardStopSparesDesktop`, `test_desktop_owned_backend_pids_classifies_by_env_and_lock`, `test_kill_stale_dashboard_processes_honours_exclude_pids` |
| 0016 | `apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx` (changed) | Switches without a dialog when installed; cancelling the install confirm doesn't switch. |

Commands:

```sh
# Python, from the repo root (or: scripts/run_tests.sh <files>)
venv/bin/python -m pytest tests/hermes_cli/test_update_fork_merge_upstream.py \
  tests/hermes_cli/test_desktop_swap_relaunch.py tests/hermes_cli/test_dashboard_lifecycle_flags.py

# Desktop: needs Node ^22.22 / ^24.11 / >=26. Vitest lives in the root node_modules.
cd apps/desktop && ../../node_modules/.bin/vitest run --project ui src/app/chat/sidebar/profile-rail-fleet.test.tsx
```

Verified on current `upstream/main` with the four patches applied: 22 Python tests and 32 vitest tests pass.

## Applying

The four patches are independent of each other and of the rest of `core/patches/`. Each applies cleanly with
`-3` on its own on current `upstream/main`, and they apply in sequence too.

A stock `hermes update` won't merge upstream into a fork that already has commits, so do the first sync by hand:

```sh
cd ~/.hermes/hermes-agent
git fetch upstream && git merge upstream/main
git am -3 --empty=keep ~/code/hermes-mods/core/patches/0007-*.patch \
          ~/code/hermes-mods/core/patches/0011-*.patch \
          ~/code/hermes-mods/core/patches/0012-*.patch \
          ~/code/hermes-mods/core/patches/0016-*.patch
git push origin main
hermes config set --force updates.fork_merge_upstream true
hermes config set desktop.relaunch_after_rebuild true
hermes desktop --build-only --force-build      # 0016 is a Desktop UI change
```

From then on, `hermes update` handles upstream for you.

## Gotchas

- **`hermes config set` refuses `updates.fork_merge_upstream` without `--force`.** 0007 does not add the key
  to `DEFAULT_CONFIG`, so Hermes treats it as unknown. `hermes config get` prints a "not a recognized config
  key" warning even though the update code does read it. You can also put it in `config.yaml` by hand.
- **The upstream sync only runs on branch `main`** and only when `origin` is not the official repo. A checkout
  on a feature branch or on a release tag never syncs.
- **0007 needs git ≥ 2.38** for `git merge-tree --write-tree`. On older git the merge fails and nothing moves.
- **Non-interactive runs never add the `upstream` remote** (launchd, `--yes`). Without it, the update syncs
  nothing and doesn't report "up to date". Add the remote by hand.
- **Merge commits are permanent history in your fork.** Don't rebase `main` afterwards. Your other machines and
  the `--force-with-lease` push both assume `main` only ever moves forward.
- **0011 does nothing on Windows**, and nothing when no Desktop main process was running from `release/`, e.g.
  a dev `npm run dev` app.
- **0011 needs `psutil`** (already a Hermes dependency). If it fails to import, the snapshot is empty and
  nothing reopens.
- **0016 needs a Desktop rebuild** before you see it: `hermes desktop --build-only --force-build`. On macOS, set
  `APPLE_SIGNING_IDENTITY` (or `desktop.macos_signing_identity`) before rebuilding, or the app is ad-hoc signed
  and macOS privacy grants reset.
- **No contract or i18n changes.** None of the four patches touch the RPC contract (`gateway-contract.generated.ts`
  / `openrpc.json`) or the locale files. The removed `'switch'` prompt copy in 0016 remains in the locale files
  but is no longer used.

## Could this be a plugin instead?

No for 0007, 0011 and 0012. They change `hermes update`, the desktop swap and `hermes serve --stop` inside
`hermes_cli`, and no plugin hooks run there.

You can get 0007's effect without forking core: run a cron or launchd script that merges `upstream/main` and
pushes before calling `hermes update`. You lose the off-tree merge and the conflict safety this patch adds.

0016 is internal state of a core sidebar hook. The desktop plugin SDK (`KEYBINDS_AREA`, `PALETTE_AREA`, etc.)
can add UI but can't remove an existing confirmation dialog, so it has to stay a core patch.


## Prebuilt desktop app (0066)

**Behavior.** With `HERMES_DESKTOP_PREBUILT_APP=<path to Hermes.app>` in its environment, `hermes update`'s desktop
step installs that bundle instead of running `npm run build` + electron-builder, which takes a few minutes. That
makes an update cheap to apply at the moment you choose, for example right after Desktop quits.

**How it works.** `install_prebuilt_desktop_app` in `hermes_cli/main_desktop.py` checks the bundle against the
same bar `_packaged_desktop_current_for_head` uses: the baked `install-stamp.json` commit is the new HEAD, the
renderer is intact, node-pty is present, and the build receipt (`scripts/build/freshness.mjs`) matches this
checkout's desktop inputs. It must also pass `codesign --verify --deep --strict` and must not downgrade the live
app's signing team. It is then copied (`ditto`) into a staging dir and promoted with the existing stage-and-swap
(`_swap_staged_desktop_app`). Any mismatch prints `Prebuilt desktop app not used (<reason>)` and the normal build
runs. The prebuilt is copied, never moved, so a failed apply can retry with it.

**Building one.** Use a detached worktree at the target commit with its **own** `npm ci`. A symlinked
`node_modules` makes `prepare-packaging-tools.mjs` refuse `app-builder-lib` as "not the source's lock-pinned
package". Then, in `apps/desktop`:
`CSC_IDENTITY_AUTO_DISCOVERY=true npm run pack -- -c.directories.output=<dir>` (bundled Node on PATH). The receipt is
content-hashed, so a bundle built in another checkout at the same commit is accepted.

**Code / tests.** `hermes_cli/main_desktop.py` (`DESKTOP_PREBUILT_APP_ENV`, `_prebuilt_desktop_app_mismatch`,
`install_prebuilt_desktop_app`), `hermes_cli/source_build.py::_build_desktop_product`. Tests are in
`tests/hermes_cli/test_source_build.py` (`-k prebuilt`): a match swaps in with no npm call; a wrong commit, a failed
signature, or no env var each builds.

**Plugin?** No. The desktop build step has no plugin hook. The companion
[core-update](../plugins/core-update/) plugin only shows the state.
