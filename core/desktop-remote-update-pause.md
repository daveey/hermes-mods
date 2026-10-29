# A remote's own update pauses SSH dials instead of redialing per request

Patch: 0605 (daveey/hermes `79f405b20b` + test follow-up `469b0731ea`, exported as one patch). Follows up on [desktop-always-on-remote-dashboard.md](desktop-always-on-remote-dashboard.md) (0538, 0548),
whose prewarm already backed off but whose request path did not.

## Why

On 2026-10-27 20:58:28–21:00:15 PDT the hub gateway (`hermes-hub`, reached over its tailnet address) ran its own
`hermes update` (process 1219818). The pooled backend `conn:hermes-hub::default` failed its dispatch probe (read
ECONNRESET) and was retired. From then on every renderer request (`GET /api/sessions/...`,
`/api/plugins/kanban/boards`, `/api/session-kanban-tasks`, ...) opened a new SSH control master, ran the remote
preflight, got "Remote Hermes update process 1219818 is still running; SSH startup is paused.", closed the master,
and the next request dialed again: 53 control masters to the hub in 108 s, and 5 failed session opens.

Five whys (evidence in `~/.hermes/logs/desktop.log`, that window):

```
Problem: 53 "opening control master to ubuntu@<hub>" lines in 108 s, each followed by "SSH startup is
  paused"; 5 [open-timing] outcome "failed".
Why 1: each request reached bootstrapSshConnection() and dialed — every refusal is preceded by "connect-start" and
  "ssh dial +N ms" for the same scope.
Why 2: no pool entry existed to share — ensureRegistryBackend() (main.ts) deletes the entry in its .catch() when the
  dial fails, so the next request starts a new dial. Concurrent requests did share one dial (three errors per dial).
Why 3: nothing remembered the refusal. The preflight's 'update-in-progress' error (remote-lifecycle.ts
  assertRemoteInstallUpdateClear) is deterministic for minutes, but was treated like any transient failure:
  thrown, forgotten.
Why 4: backoff lived only in remote-prewarm.ts (its own per-target state), not at the dial chokepoint, so only one of
  the paths that dial had it ("[remote-prewarm] ... retry 1 in 2000 ms" next to per-request redials).
Root (happened): retry policy was owned by one caller instead of by the dial; a known-long refusal had no negative cache.
Root (not caught): the 0538 tests (remote-warm-dispatch.test.ts) drive RemotePrewarmer with a mocked `ensure` and
  pin only prewarm's own backoff; nothing ran repeated requests through the pool's delete-on-failure path, and the
  remote-lifecycle tests assert one refused dial, not what the next request does.
Recurrence: same class as #103288 (boot re-driving a failing ssh probe every ~2 s, fixed by tagging that error
  terminal); that fix was local to boot, so the per-request path kept the pattern.
```

## What it does

- **`electron/remote-update-pause.ts`** (`RemoteUpdatePause`, Electron-free). `run(key, dial)` wraps a dial. A dial
  that fails with `kind === 'update-in-progress'` pauses the key: later callers get the cached refusal at once
  (same message, `kind 'update-in-progress'`, `code 'remote-update-paused'`, `retryAt`), with no SSH dial, until the
  window passes (`UPDATE_PAUSE_BACKOFF_MS` 2, 5, 10, 20, 30 s, capped). Then exactly one caller probes: a refusal
  advances the backoff, a success clears the pause and logs
  `[remote-update-pause] <key> remote update finished; reconnected after N refused probe(s), paused M ms` once, any
  other failure ends the pause so it never masks a new problem. A refusal from a dial that started before the pause
  was recorded (a parallel first dial for another profile) does not advance the backoff.
- **Key = the remote install** (`remoteUpdatePauseKey`: `user@host:port|remoteHermesPath`): the update marker lives
  under the remote hermes home, so every profile on that host pauses and recovers together.
- **One chokepoint.** `bootstrapSshConnection()` in `main.ts` (every registry, primary and transient SSH dial) runs
  through the gate. A managed update restore (`metadata.managedUpdateCorrelation` set) bypasses it: the Desktop
  updated that install itself and owns its own gate.
- **Prewarm shares the schedule.** `RemotePrewarmer` (`remote-prewarm.ts`) uses an error's `retryAt` as its next
  attempt instead of its own backoff, so its 15 s tick is the probe that sees the update finish even with no
  renderer traffic.

## Tests

`electron/remote-update-pause.test.ts`, one per entry path, with a fake clock and a harness that reproduces
`ensureRegistryBackend()`'s pool (shared in-flight entry, deleted on failure) around the gate:

- **Renderer API requests**: 360 requests over 120 s (3 per second across two profiles) cause at most one dial per
  backoff window (under 10 dials, each probe at least the next backoff step after the last); all fail with the
  refusal text. After the update ends, the next request dials once and succeeds, the pause clears, the recovery
  log appears once, and other profiles dial normally.
- **remote-prewarm**: prewarm ticks every 15 s plus a renderer request every second for 3 minutes share the same
  windows; with renderer traffic stopped, prewarm alone reconnects after the update ends, exactly one dial, one
  "reconnected" prewarm line and one recovery line.
- The paused error keeps the refusal text and carries `retryAt`; a probe that fails some other way ends the pause.
- `main.ts wiring`: `bootstrapSshConnection` routes through `remoteUpdatePause.run(remoteUpdatePauseKey(...))`
  and bypasses it for managed restores.

The three behaviour tests fail with `run()` made a passthrough (the pre-patch behaviour) and pass with it.

## Gotchas

- Before the first refusal nothing knows the host is updating, so parallel first dials (one per profile in flight)
  still happen once. After that, one dial per window per host.
- Recovery lag is at most the current window (30 s cap) plus the next caller: a renderer request or the next prewarm
  tick (15 s).
- Generic SSH failures (host down, auth) are not cached by this gate; the request path still redials for those.
  Only prewarm backs off on them.

## Follow-up patches (0606)

**0606** (`469b0731ea`): the pause test now uses a documentation address for the hub instead of a real one (`electron/remote-update-pause.test.ts`); no behaviour change.
