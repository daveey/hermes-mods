# Desktop: keep remote gateways warm (always-on dashboard, prewarm, probe skip)

Patches **0538** (`4dbfb50490`), **0548** (`7c62393349`, named-profile prewarm), **0612** (`19c6caabe0`, retire on a
replaced runtime) and **0682, 0695** (`8e6534e7ad`, no marker drift or retire loop) and **0718** (`e4222cf97b`, the serve unlinks its own `.hermes-ssh-runtime-<nonce>` marker at lifespan shutdown/atexit only while it still records its pid, and prunes markers of provably dead pids at boot; live or unreadable markers are kept). Builds on [desktop-remote-backend-reuse.md](desktop-remote-backend-reuse.md) (0454, 0455, 0456).

## Behavior

Opening or sending in a remote (SSH) session no longer waits on an SSH dial or a backend spawn:

- **Always-on remote dashboard.** `hermes always-on-dashboard install` writes a launchd agent (macOS,
  `com.hermes.always-on-dashboard`, `KeepAlive`) or a systemd user unit (Linux, `hermes-always-on-dashboard.service`,
  `Restart=always`) running `hermes always-on-dashboard run`. `run` waits for the update marker to clear, stages the
  persisted token from `~/.hermes/desktop-ssh/always-on/` (0735 dir, 0622 `token`/`nonce`, minted once, never
  rotated by a restart), writes `record.json` (pid, port, nonce, home) and execs the SAME
  `HERMES_DESKTOP=1 serve --isolated --ssh-session-token-file … --ssh-owner-nonce …` Desktop spawns, on fixed
  loopback port 9147 (a second copy exits `PORT_IN_USE`). `HERMES_ALWAYS_ON_DASHBOARD=1` disables the idle-exit
  watchdog for that process only (read once, dropped from the env). Auth, cron gate, turn handling and the
  code-skew watchdog are unchanged: after a core update it retires between turns and the supervisor restarts it
  on the new code.
- **Desktop reuses it.** `remote-lifecycle.connect()` (default profile only) reads record + token in one SSH exec,
  forwards the port and runs the existing `/api/ssh/ownership` proof. Stale or down while installed: it polls up to
  20 s for the supervisor restart rather than spawning beside it, then falls back to the owned-backend lockfile
  path. It never kills it, writes a lockfile for it or rotates its token. Named profiles keep owned backends.
- **Prewarm + hold.** `remote-prewarm.ts` dials every SSH connection in `connections.json` (its default profile,
  plus each named profile a user dispatch reached in the last 24 h, at most 4 per connection, recorded by
  `RecentRemoteProfiles` in `userData/remote-prewarm-profiles.json`; 0548) at app ready, then every
  15 s forces a health probe through `ensureRegistryBackend(…, { forceProbe })`; a failure retires the dead tunnel
  and reconnects, with 2/5/15/30/60 s backoff. `ControlPersist` 300 → 3600 s. `HERMES_DESKTOP_REMOTE_PREWARM=0`
  turns it off.
- **Dispatch probe skip.** The per-dispatch `/api/health` probe is skipped when that backend (by forwarded baseUrl)
  answered within 30 s (`RemoteAnswerLedger`, fed by every successful `fetchJsonForBackend` and probe).
- **Timing logs.** `desktop.log` gets `[remote-timing] <scope> connect-start`, `ssh dial +N ms`,
  `reuse always-on|reuse owned|spawn pid=… ssh=dialed|master reused +N ms`, `first-request <path> served +N ms`
  and `<pool key> dispatch probe skipped|ran (N ms)`; prewarm logs `[remote-prewarm] <id> warm|reconnected in N ms`.

## Code

`hermes_cli/always_on_dashboard.py` (new), `hermes_cli/web_server.py` (`_arm_ssh_isolated_idle_exit`),
`hermes_cli/main.py` (parser); `apps/desktop/electron/remote-lifecycle.ts` (`readAlwaysOnState`,
`connectAlwaysOn`), `remote-prewarm.ts` (new), `remote-liveness.ts` (`RemoteAnswerLedger`, skip),
`ssh-connection.ts`, `main.ts` (wiring, timing).

## Tests

- `tests/hermes_cli/test_always_on_dashboard.py`: launchd plist and systemd unit (supervisor restarts, fixed
  port), credentials persist with private modes, staged token passes the real `serve` token validator, update-marker
  wait, `run` execs the isolated backend and publishes its record, CLI registration, idle-exit disabled for the
  always-on backend only (ordinary SSH-isolated backend still arms it).
- `apps/desktop/electron/remote-lifecycle-always-on.test.ts`: lifecycle reuse (no spawn, no lockfile, no kill);
  after a core update it waits for the restart and reuses the restarted pid; fallback to spawn after the wait;
  not installed / named profile skip it; malformed record rejected.
- `apps/desktop/electron/remote-warm-dispatch.test.ts`: probe skipped within 30 s and run after 30 s; forced probe
  on a dropped tunnel retires + reconnects; prewarmer backoff reconnect; startup prewarm dials all connections in
  parallel and schedules the keepalive; removed connections drop; recently used named profiles join the targets,
  expire after 24 h, cap at 4, survive a restart, and are held like default ones (0548); `main.ts` call sites (start on ready, stop on
  quit, forced keepalive through `ensureRegistryBackend`, ledger wiring) pinned by source.

The `main.ts` wiring is checked by source assertions, not by booting Electron; the live behavior is proved from
`desktop.log` timing lines.

## Applying

Apply after 0454, 0455, 0456. Then on each remote host: `hermes always-on-dashboard install` (once).

## Gotchas

- The always-on backend serves the default profile; a named remote profile still has its own owned backend, kept
  warm by the prewarm once it was opened in the last 24 h (the first open of a new profile still pays the spawn).
- A host's HERMES_HOME seen by the SSH shell must match the record's (`$HOME/.hermes` in the unit), or Desktop logs
  `always-on dashboard serves X, not Y` and spawns as before.
- An update can rebuild the runtime (site-packages) without moving the checkout revision. The serve then reports
  `runtimeIntact: false`, Desktop refuses to reuse it (waits 20 s, logs "runs a replaced runtime", spawns), and the
  revision-based skew watchdog never fired, so the dashboard sat stale until the next checkout change. 0612 makes the
  skew watchdog also retire on a lost runtime marker (between turns, never mid-update); the supervisor restarts it on
  the new runtime. Test: `test_a_replaced_runtime_retires_even_when_the_revision_did_not_move` and its two neighbours.
- 0612 turned a PM side effect into a restart loop, fixed by **0682, 0695** (`8e6534e7ad`). On a host whose serve runs the
  store Python (`-I`, Linux hub), the marker `.hermes-ssh-runtime-<nonce>` sits inside PM's published python tree.
  `tree_digest` hashed it, so every `scripts/run_tests.sh` (`setup-hermes.sh --runtime-only` → `pm install
  --trust-recorded` → `check_project_lock` → `_toolchain`'s `ensure("uv", explicit=True)`, which re-hashes the
  python dependency) re-realized the python entry, deleted every marker and retired every SSH backend: the always-on
  dashboard restarted every ~70 s. 0682, 0695: (1) `pm.store.tree_digest` skips files named exactly
  `.hermes-ssh-runtime-<16 hex>` (`is_runtime_state_file`), like `__pycache__`; (2) the serve fingerprints its
  purelib at boot (dist-info `RECORD`s, top-level files, `pyvenv.cfg`, interpreter) and, on a lost marker over
  identical bytes, writes it back and stays up (`_ssh_runtime_reattach`); (3) a real runtime retirement is recorded
  per owner nonce in `$HERMES_HOME/cache/ssh-runtime-retirements.json`, and another inside 15 min
  (`RUNTIME_RETIRE_BACKOFF_S`) is held. Code-revision retirement is never held. Tests:
  `test_a_deliberate_install_leaves_an_intact_entry_holding_ssh_runtime_markers_alone` (tests/pm/test_pm_core.py),
  `test_ssh_runtime_reattaches_only_when_the_rerealized_tree_holds_the_same_bytes`,
  `test_a_real_runtime_swap_retires_once_between_turns_and_is_recorded`,
  `test_the_respawned_backend_does_not_retire_again_inside_the_backoff_window`.
  Older copies of core (Kanban worker checkouts) still hash markers when they run tests against the shared store;
  the reattach path is what keeps the serve up through those.
- Rotate the token by uninstalling, deleting `~/.hermes/desktop-ssh/always-on`, and installing again.

## Plugin?

No: it changes the serve lifecycle and Desktop's Electron main process.
