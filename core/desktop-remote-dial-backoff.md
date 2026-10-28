# A down remote fails fast instead of redialing SSH per request

Patch: 0597 (daveey/hermes `59a927377a`). Extends [desktop-remote-update-pause.md](desktop-remote-update-pause.md) (0592), whose gate cached only
a remote's own-update refusal, to every SSH dial failure a redial seconds later cannot change. Prewarm
([desktop-always-on-remote-dashboard.md](desktop-always-on-remote-dashboard.md), 0526/0535) shares the schedule.

## Why

On 2026-10-27 16:53:11–16:55:28 PDT the Mac mini stopped answering on its tailnet address. Desktop opened 11 SSH
control masters to it in 2 min 17 s, each failing `kind=timeout ... ssh timed out after 15000ms`, and 36 renderer
requests (`/api/plugins/inbox/items`, `/api/plugins/hub-sync/status`, `/api/session-kanban-tasks`, ...) each waited
up to 15 s before failing with "SSH operation to ... timed out". 0592's Gotchas named the gap: generic failures
still redial per request.

Five whys (evidence in `~/.hermes/logs/desktop.log.1` and `desktop.log`, that window):

```
Problem: 11 "opening control master to daveey@<mini>" lines 16:53:11-16:55:28, each followed 15 s later by
  "connect ... failed (kind=timeout ...)"; 36 [hermes:api ...] "SSH operation ... timed out" errors.
Why 1: each request dialed. The next control master opens ~20 ms after the previous failure
  (16:54:43,344 failed -> 16:54:43,362 opening), i.e. the next queued request, not a retry timer.
Why 2: no pool entry existed to share. ensureRegistryBackend() (main.ts) deletes the entry in its .catch() when the
  dial fails, so the next request starts a new dial. Concurrent requests did share one dial (several errors with the
  same millisecond).
Why 3: nothing remembered the failure. 0592's gate (remote-update-pause.ts) cached only kind 'update-in-progress';
  the 'timeout' error that ssh-connection.ts _fail() tags was thrown and forgotten.
Why 4: there was no written rule for which dial failures are worth remembering. 0592 fixed the failure kind its
  incident showed and listed the rest as a gotcha, so each further kind would need its own incident; only
  remote-prewarm.ts backed off on them (its own state, its own caller).
Root (happened): the dial chokepoint had no classification of failures; retry policy for a down host lived in one
  caller (prewarm) instead of at the dial.
Root (not caught): no test sends repeated requests at a host that times out or refuses. 0592's tests drive only the
  update refusal; 0526's drive prewarm with a mocked ensure; ssh-connection tests assert how one failed dial is
  classified, not what the next request does.
```

## Rule: which failures the gate remembers

`classifyRemoteDialFailure()` in `electron/remote-update-pause.ts`, pinned by a test table:

| Class | Error `kind` | Why |
|---|---|---|
| `update` (0592) | `update-in-progress` | The remote refuses until its own `hermes update` ends. Backoff 2, 5, 10, 20, 30 s. |
| `unreachable` | `unreachable`, `timeout`, `transient-transport-error` | Network: host down, refused, no route, SSH timeout, transport dropped mid-preflight. Decided outside Desktop; the host can come back by itself. |
| `unreachable` | `auth-failed`, `interactive-auth`, `host-key-changed` | SSH refused us. Every redial gets the same answer until the user fixes it outside Desktop (ssh-add, known_hosts, a Tailscale check); the probe notices that fix within one window. Boot still latches these as terminal on the primary path. |
| not remembered | `ssh-client-failed` (#103288) | Local client broken. Raised by `ssh -G` before the gate and latched by boot; handling unchanged. |
| not remembered | `superseded` | A caller cancelled the dial. |
| not remembered | `spawn-failed`, `ready-timeout`, `ownership-changed`, `remote-lockfile-skew`, `remote-profile-missing`, `hermes-not-found`, `update-required`, ... | The dial worked; the remote answered with a state the next attempt is meant to act on. |
| not remembered | `unknown`, untagged | An unrecognised failure is never masked by a cached one. |

`unreachable` backs off 2, 5, 15, 30, 60 s (capped), the same steps as prewarm's own backoff, so prewarm's
schedule does not change.

## What it does

- **Same gate, second class.** `RemoteUpdatePause.run(key, dial)` (key = remote install, shared by every profile on
  the host) records a remembered failure with its class. Later callers fail fast with no dial: the error keeps the
  original message, `kind` and `sshError` (so boot's auth / host-key / retryable classifiers read it the same) and adds
  `code 'remote-unreachable-backoff'`, `retryAt`, `cause`. When the window opens exactly one caller probes. Success
  clears the state and logs `[remote-dial-backoff] <key> reachable again; reconnected after N failed dial(s), down
  M ms` once. A probe that fails with the other class switches class (backoff restarts); one that fails with an
  unremembered kind ends the state.
- **`retryAt` on the first failure too.** The gate stamps `retryAt` on the error it records, so prewarm waits for the
  gate's window from the first failure on, not only on cached errors.
- **Explicit user retries dial right away.** `remoteUpdatePause.clear(why)` drops every remembered host and logs
  `[remote-dial-backoff] cleared <keys> for <why>; the next request dials now`. Called from the boot overlay's
  Reload and retry (`hermes:bootstrap:reset`), Repair (`hermes:bootstrap:repair`), Gateway settings apply
  (`hermes:connection-config:apply`), saving a connection (`hermes:connections:save`) and a reachable Test
  (`hermes:connections:test`). The registry Reconnect action (`hermes:connections:reconnect`) and managed update
  restores carry a correlation id and bypass the gate as before.
- **Unchanged:** managed restores bypass; `sshClientFailedError` (#103288) never reaches the gate and boot latches
  it; prewarm follows `retryAt` (comment widened).

## Tests

`electron/remote-dial-backoff.test.ts`, next to 0592's `remote-update-pause.test.ts`, with a fake clock and the
same harness shape (`ensureRegistryBackend()`'s pool, shared in-flight entry, deleted on failure, around the gate):

- **Renderer requests**: 540 requests over 3 minutes against a timing-out host (3 a second, two profiles) make fewer
  than 10 dials, at most one per backoff window and each probe at least the next step after the last; every request
  fails with the timeout text and `kind 'timeout'`. Once the host is back, the next request after the window dials
  once, 20 more requests reuse it, the recovery line logs once, and another profile dials normally.
- **remote-prewarm**: a 15 s prewarm tick plus a renderer request every second for 3 minutes share the same windows;
  with renderer traffic stopped, prewarm alone reconnects with exactly one dial, one prewarm "reconnected" line and
  one recovery line.
- **Explicit reconnect bypass**: inside a window a request does not dial, but `clear('reload and retry')` lets the
  next request dial at once (and restarts the schedule while still down); with the host back the retry reconnects
  with one dial. A managed-correlation dial (registry Reconnect, managed restores) dials inside the window and its
  entry then serves requests without another dial.
- **The rule**: every remembered kind classifies `unreachable`; `ssh-client-failed`, `superseded`, `spawn-failed`,
  `ready-timeout`, `ownership-changed`, `remote-lockfile-skew`, `remote-profile-missing`, `hermes-not-found`,
  `update-required`, `unknown` and untagged errors dial on every request and never pause the host;
  the ssh-client failure reaches the caller as the same object and `isSshClientFailedBootFailure` still holds.
- A remote paused for its own update whose probe then times out switches to the `unreachable` schedule (cached
  error `remote-unreachable-backoff`, window restarted at 2 s).
- A cached `auth-failed` error still satisfies `isSshAuthFailedBootFailure` and carries `retryAt`; a probe that fails
  with an unremembered kind ends the backoff.
- **main.ts wiring**: each explicit-retry IPC handler calls `remoteUpdatePause.clear(...)`.

With the `unreachable` class disabled (the 0592 behaviour) six of the nine tests fail; with `clear()` a no-op the
explicit-reconnect test fails. 0592's tests pass unchanged.

## Gotchas

- `clear()` drops every remembered host, not just the one the user retried: explicit retries are rare, and another
  still-down host only costs one extra dial.
- Recovery lag for a down host is at most the current window (60 s cap) plus the next caller: a renderer request
  or the next prewarm tick (15 s). Reload and retry skips the wait.
- A remote that answers the dial but fails later (spawn, ready timeout) is still retried per request; those failures
  carry remote state the next attempt is meant to act on and are out of this gate's rule.
