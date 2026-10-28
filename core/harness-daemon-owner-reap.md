# Named browser-harness daemons stop when their owner dies or goes idle

Patches **0581** (source `4c6a424f63`) and **0582** (source `c51212b4d0`).

## Behavior

- Before: every `browser_exec(session="<name>")` started a `browser_harness.daemon` for that `BU_NAME`. Each one is
  detached (`start_new_session`), has no idle exit, and holds a browser-level CDP connection to the agent Chrome.
  Nothing stopped it when the agent that started it went away. On 2026-10-27, 18 of them were found re-parented to
  launchd (ppid 1), the oldest about 5 days old.
- Now (0581) each `browser_exec` on a named daemon stamps `<harness runtime>/hermes-owners/<BU_NAME>/<pid>-<start>`
  (pid plus process start time, so a recycled pid does not count as the same owner). The reaper stops a stamped
  daemon through the harness's own identity-checked `--reload` when no stamp names a live process, or when no owner
  has driven it for `browser.harness_daemon_idle_s` seconds (default 7200; `0` turns the idle rule off). It runs from
  `browser_exec` at most once a minute, and at every Hermes process exit, after that process drops its own stamps. A
  daemon that a live sibling process still drives is kept.
- The shared `default` daemon is never stamped or reaped. It is the one daemon that normal work is meant to reuse.
- Now (0582) the `session` parameter's description tells the model to omit it for normal work, and to set a name only
  for a browser task that runs at the same time as another one. Before, it invited a name "so concurrent tasks don't
  share tabs".

## Root cause (five whys)

```
Problem: 2026-10-27, 18 `python3.14 -m browser_harness.daemon` processes with ppid 1 (oldest ~5 days, ~20 MB each,
  each holding a CDP connection to the agent Chrome on 127.0.0.1:9333). 14 were killed by hand; 4 were left
  (bu-spend, bu-alpha-askeve, bu-weco-auth, bu-default). The runtime dir held 50+ bu-<name>.pid/.sock pairs.
Why 1: each daemon serves a different BU_NAME — evidence: `ps eww` env shows BU_NAME=spend / alpha-askeve /
  weco-auth plus BH_AGENT_WORKSPACE=.../workspace/<session id>, the env browser_exec builds.
Why 2: agents passed a fresh `session` name per task — evidence: state.db, last 14 days, 1,559 browser_exec calls;
  names include nous-portal (33), alpha-askeve (31), ghapp (26), xdm (25), spend (6). Session 20261026_175539_2e6c86
  called `browser_exec(local=true, session="spend", ...)`.
Why 3 (happened, instructions): the tool schema invited it — evidence: browser_use_cli.py BROWSER_EXEC_SCHEMA
  `session`: "Named isolated browser session — its own daemon ... so concurrent tasks don't share tabs". The
  browser-use plugin's own rule (omit BU_NAME, reuse the default daemon) is not in Hermes's tool text.
Why 4 (survived): nothing stops a named daemon when its owner goes away —
  a) the harness spawns daemons with start_new_session (browser_harness/_ipc.py spawn_kwargs) and the daemon has no
     idle timeout (browser_harness/daemon.py, no idle path);
  b) the clean-exit hook `_emergency_cleanup_all_sessions` only calls `cleanup_all_browsers()` (which calls
     `stop_harness_daemons`) when `_active_sessions` is non-empty, and the CDP-override route these daemons took
     (BU_CDP_WS=ws://127.0.0.1:9333/..., `_resolve_backend_cdp` step 2) never adds an `_active_sessions` entry;
  c) the owners were a Kanban worker and Desktop serve children (HERMES_SPAWN=v1:-:serve:<pid>); their parent pids
     (24248, 62696, 98530, 67362) were gone, and a SIGKILLed or scope-killed process runs no atexit at all.
Root (happened): daemon lifetime was tied to an explicit stop call on one exit path, not to the owner's liveness.
Root (not caught): no test asserted that a daemon dies with its owner; the agent-browser orphan reaper
  (`_reap_orphaned_browser_sessions`, owner_pid files) covers agent-browser daemons only, never harness daemons.
Recurrence: partial. 0371 (managed-chrome-leases) retires harness daemons for managed leases only (opt-in,
  experimental); the plain CDP/local route kept leaking.
Countermeasures:
- symptom: 3 leftover named daemons stopped with `BU_NAME=<name> browser_harness.run --reload`.
- root: owner stamps plus reaper (0581) — proof: tests below; real-harness repro (an owner SIGKILLed, its daemon
  re-parented to ppid 1, the next reap stops it).
- instructions: `session` description says omit it (0582) — proof: prompt-surface snapshot updated with it.
- detection: test_sigkilled_owner_leaves_daemon_the_next_reap_stops reproduces the leak with real processes.
```

## How it works

`record_daemon_owner(env, name)` touches `<runtime>/hermes-owners/<name>/<pid>-<start>`. The runtime dir is the
harness's own (`BH_RUNTIME_DIR`, else `BH_HOME`/`BROWSER_HARNESS_HOME`/`XDG_CONFIG_HOME`/`~/.config/browser-harness`,
plus `runtime`), so every Hermes profile on the host sees the same stamps for the same daemons.
`reap_orphaned_harness_daemons(env, drop_own=False)` deletes stamps whose pid is gone or whose start time differs.
A name with no live stamp, or whose newest live stamp is older than the idle window, gets `--reload`. Then its stamp
dir is removed and the name leaves `_driven_daemons`. `browser_exec` stamps first and then reaps (throttled to
`_REAP_INTERVAL_S` = 60 s), so its own daemon is fresh when it is checked. `_emergency_cleanup_all_sessions` calls
the reaper with `drop_own=True` unconditionally.

## Code

- `tools/browser_use_cli.py`: `_harness_runtime_dir`, `record_daemon_owner`, `_owner_alive`,
  `reap_orphaned_harness_daemons`, `_maybe_reap_harness_daemons`, the stamp+reap call in `browser_exec`'s dispatch,
  and the `session` description (0582).
- `tools/browser_tool_lifecycle.py`: `_reap_harness_daemons_on_exit` in `_emergency_cleanup_all_sessions`.
- `tests/fixtures/prompt_surface/tools/browser_exec.json` (0582).

## Tests

`tests/tools/test_browser_use_daemon_reap.py` (7 passed via `scripts/run_tests.sh` on the Mac):
- `test_live_owner_keeps_daemon_dead_owner_reaps_it`: a stamp of an exited pid → `--reload` for that name; a live
  owner's daemon is kept.
- `test_recycled_pid_with_other_start_time_is_dead`: same pid, different start → reaped.
- `test_default_daemon_is_never_stamped`.
- `test_idle_daemon_is_reaped_even_with_live_owner`: idle window, live owner.
- `test_exit_drops_own_stamp_but_keeps_daemon_a_live_sibling_holds`: the exit path (`drop_own=True`).
- `test_browser_exec_stamps_its_named_daemon`: the `browser_exec` entry path writes the stamp.
- `test_sigkilled_owner_leaves_daemon_the_next_reap_stops`: real processes. An owner subprocess runs `browser_exec`
  through a stand-in CLI that spawns a detached daemon, is SIGKILLed (no atexit), the daemon is still alive, and the
  reaper in another process stops it.

The atexit hook wiring (`_emergency_cleanup_all_sessions` → reaper) has no dedicated test; the exit behavior is
tested by calling the reaper with `drop_own=True`. The existing browser suites (`test_browser_use_cli.py`,
`test_browser_cleanup.py`, `test_browser_orphan_reaper.py`, `test_browser_agent_*`, 500+ tests) pass.
`test_prompt_surface.py` cannot render on macOS (`_sysconfigdata__linux_darwin`); CI on Linux checks 0582's snapshot.

A manual reproduction with the real harness against the agent Chrome (127.0.0.1:9333) gave: owner pid 76791 drove
`session="reaprepro"`, daemon 76851; `kill -9` the owner → the daemon showed ppid 1; `reap_orphaned_harness_daemons()`
from a new process returned `['reaprepro']` and daemon 76851 was gone.

## Applying

`git am -3` 0581 then 0582. 0581 needs `stop_harness_daemons`/`_driven_daemons` (upstream browser-use CLI backend)
and `gateway.status.get_process_start_time`.

## Gotchas

- Daemons started before 0581 have no stamps and are not reaped. Stop them once by hand.
- A daemon driven from a terminal `browser-use` with `BU_NAME=...` (not `browser_exec`) is not stamped, so it is never reaped.
- A gateway that hosts many sessions is one owner pid. Its named daemons are stopped by the idle window, not by owner death.
- A name reused after a reap just respawns its daemon. The tab pin preamble is keyed by daemon pid, so the new
  daemon gets its own tab.
- Takes effect for each Hermes process after it restarts on the new code.

## Plugin?

No. It hooks `browser_exec`'s dispatch and the browser atexit cleanup inside core.
