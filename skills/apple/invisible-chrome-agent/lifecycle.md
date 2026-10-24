# Managed Chrome leases (opt-in, experimental)

This extends the existing focus proxy; it does **not** replace the passkey notifier,
focus guard, virtual display, anti-backgrounding flags, or saver preferences. Existing
installations without `cache/chrome-agent/lifecycle-config.json` retain always-on
raw CDP behavior. Only `install --managed` creates that file; a default reinstall
does not migrate a legacy installation.

## Behavior

- A managed supervisor starts its loopback listener without Chrome or the virtual
  display. Only an authorized `POST /lifecycle/acquire` starts them. GET discovery,
  status polling, open sockets, auto-attach, and re-homing never renew a lease or wake Chrome.
- One owner is exactly `[profile_home, task_id, session]`. Each has an opaque
  capability, unique daemon name, and independent request tokens. Acquiring again
  for an active owner reuses its capability, not its request token. Completion
  applies only to that token; releasing invalidates the capability immediately.
- `/lease/<capability>/json/version`, `/json/list`, browser and page WebSockets
  expose owned targets only. Creation binds the exact `Target.createTarget`
  response ID; attach is restricted to existing owned IDs. There is no target-list
  diff inference or URL-based adoption. Chrome-reported current-generation opener
  lineage binds new owned popups, including sign-in windows, to the opener's owner.
  Invalid scoped requests never fall back to raw CDP.
- The actual managed listener refuses every unscoped HTTP/WebSocket route,
  including raw discovery, create, close and activate, without waking Chrome or
  forwarding upstream. Only `/lifecycle/` and `/lease/` are served. Ordinary raw
  tools cannot create unowned tabs through this port; unconfigured legacy proxies
  retain their original behavior.
  Unknown pre-existing targets block browser idle shutdown and are never automatically closed.
  Managed launches use `--no-startup-window` instead of an unowned bootstrap blank;
  legacy always-on launches retain their initial blank tab.
- Owned expired tabs are closed through CDP, not by killing renderers. Defaults:
  lease idle 1800 seconds, release/orphan grace 300, browser idle 120, handoff 1200.
  Each request protects through `timeout_s + 30` seconds, including after ledger reload.
- The version-1 ledger uses a process/thread lock, atomic replace, fsync, and mode
  0600. Browser generations invalidate bindings. Invalid ledgers fail closed;
  they are not replaced with an empty ledger. Only one supervisor holds the lifetime lock.
  Validated memory is authoritative; ownership reads do not parse or serialize the file.
  Safe history and expired requests are pruned. Limits are 8192 leases, 16384 targets,
  4096 downloads, 256 requests/workers per owner, and 16 MiB on disk; oversized
  mutations are refused without replacing valid state. Download progress is coalesced
  at five-second intervals; start and terminal events persist immediately.
- Human show/passkey reveals protect known owned targets with a bounded persisted
  deadline. Downloads are tracked by GUID; increasing bytes refresh a 120-second
  stall deadline, capped at 3600 seconds from first observation. Progress never
  refreshes the lease itself. Downloads survive closure of their initiating tab.
  Main-frame download navigation falls back to its bound target ID when no frame
  navigation event committed. Persisted deadlines survive reload.
  A new show/reveal episode renews protection; polling the same episode never does.
  Show protection ends at the earlier of its requested deadline and `handoff_s`
  from the explicit show event. An indefinite show is therefore not indefinite protection.
- The listener remains alive after idle shutdown. Concurrent acquires serialize
  wake. A deliberately stopped/disabled launchd job is not auto-bootstrapped.

### Elapsed time and persisted clocks

Lease activity, request/download/handoff deadlines and browser idle intervals use
a monotonic runtime clock, not wall-clock elapsed time. Backward wall jumps cannot
extend orphans and forward jumps cannot prematurely expire active protection.
The optional version-1 `clock` object stores `{boot, logical, monotonic}` at each
durable mutation. `logical` timestamps keep an epoch-shaped origin but are not
current civil time after a wall adjustment. Linux uses its boot UUID; macOS uses
`kern.bootsessionuuid` (not a wall-time-derived boot date). Missing boot identity
refuses managed startup rather than guessing elapsed time.

Same-boot reload consumes monotonic downtime, preserving exact deadlines even
after repeated supervisor crashes. Across boots (or a reset monotonic counter),
elapsed downtime cannot be proven safely: preserve only the remaining bounded
durations at the last snapshot, then immediately persist the new clock anchor.
This deliberately does not count power-off time; it is a bound in running clock
time, not a promise of expiry during arbitrarily repeated machine reboots.
Legacy ledgers without a clock anchor get one conservative migration: activity
and release start at migration, existing requests receive at most 86400 seconds
plus configured grace, and protection gets at most its configured handoff/stall/
download maximum. This can delay legacy cleanup but never destroy a potentially
active operation because the wall clock jumped forward. Migration is persisted
even on the first readonly access, so ordinary restarts do not renew it.
Corrupt/nonfinite anchors fail closed. Explicit show wall deadlines are converted
to a remaining monotonic duration per episode; polling the same episode, target
discovery, autoattach and open sockets never renew it.

## Install / configure

On a Mac, with explicit operator approval:

```sh
./scripts/hermes-chrome-agent install --managed --no-start
# Builds helpers and writes the job/config; disables and unloads the supervisor.
./scripts/hermes-chrome-agent activate
./scripts/hermes-chrome-agent stop
./scripts/hermes-chrome-agent disable
```

`install` without `--no-start` also activates. `activate` is explicit and re-enables
an operator-disabled job. Both `stop` and `disable` disable then unload, preserving
OFF across logins. Incompatible managed configuration exits cleanly, not in a
KeepAlive crash loop. Launchd
restarts unsuccessful exits, not a graceful SIGTERM/SIGINT exit. No wake helper or
private installation manifest is supplied by this module.

Private agent-local configuration at
`$HERMES_HOME/cache/chrome-agent/lifecycle-config.json`:

```json
{
  "installation_id": "operator-generated-opaque-identity",
  "settings": {
    "idle_lease_s": 1800,
    "orphan_s": 300,
    "idle_browser_s": 120,
    "handoff_s": 1200,
    "request_grace_s": 30,
    "download_stall_s": 120,
    "download_max_s": 3600
  }
}
```

Managed install generates the identity once; reinstall preserves it. Settings are finite
numbers between 1 and 86400 seconds; booleans and unknown keys are rejected.
The supervisor requires owner-only configuration permissions and the dedicated
`$HERMES_HOME/chrome-agent` profile, rejecting a different profile or redirected symlink.
Changing settings requires a deliberate supervisor restart. SIGTERM/SIGINT rejects
new acquires immediately upon entering drain, permits completions, and waits up to
30 seconds for active requests/downloads/handoffs. Explicit stop ultimately wins:
owned harness daemons get a separate 10-second total verified IPC retirement
budget after drain, then browser/display children are terminated. A shutdown ACK
alone is not success: process retirement must be observed. Silent, replaced or
unverifiable sockets are not signalled or deleted; their retry records remain
and the remaining count is logged. Idle supervisors retry ordinary eligible
daemon cleanup without requiring a browser wake. Cached process exit is not endpoint
retirement: the current private capability socket is inspected afresh before a
replacement is tracked. A still-live old process remains tracked even when its
socket has changed; unknown replacements remain unresolved, never sent shutdown.
Socket/process identity is checked again on the connected IPC channel before
shutdown (including peer credentials on Linux), and vacancy is rechecked before
removing the record. Verified daemon identities carry an optional `socket_stamp`
(device, inode, nanosecond ctime) within the existing v1 ledger. This distinguishes
an abandoned verified socket from an unverified replacement that refuses connects;
connection refusal alone never proves an unknown process exited. Live legacy
identities are re-inspected to add the stamp. A dead legacy identity with an
extant uninspectable socket remains unresolved; missing endpoints can be cleared.

Each candidate receives at most 0.5 seconds of the sweep's total budget (5 seconds
for idle cleanup, 10 seconds for explicit retirement). A capability-order cursor
rotates across successive sweeps in the same supervisor, including when a prior
record was removed; a candidate is not skipped merely because the previous attempt
used almost all the budget. This prevents silent/ACK-without-exit owners starving
later owners. Large populations can require multiple idle sweeps; explicit stop
still ends at its total deadline and reports unresolved records. The cursor is
in-memory scheduling state, reset on supervisor restart, not a new ledger schema.
Startup and child teardown retain their own bounded waits. A disabled installation
is never automatically enabled.
The core's separate opt-in is `browser.managed_agent.endpoint` (the exact raw
loopback endpoint) and `browser.managed_agent.installation_id` (this identity).
Do not infer management from a port number. This file is configuration, not a
core wake-helper manifest.

## Control API

All control calls use JSON POST to the raw endpoint. Acquire:

```json
{"installation_id":"configured-identity","owner":["/profiles/example","task-example","session-example"],"timeout_s":300}
```

Response:

```json
{"endpoint":"http://127.0.0.1:19001/lease/opaque-capability","capability":"opaque-capability","request_id":"opaque-request","daemon_name":"lease-opaque-capability"}
```

Complete at `/lifecycle/complete` with `installation_id`, `capability`, and
`request_id` in `finally`, including execution errors/timeouts. Release at
`/lifecycle/release` with `installation_id` and `capability`. Release must be
owner-scoped, not process-wide when one session ends. Unknown/late completion
returns `{"completed":false}`. Repeated release is idempotent.

## Known limitations / acceptance still required

**Not production-verified. Isolated Linux headless Chrome and core browser-use
default/local entry smoke checks passed. Mac headful acceptance is still required.**

- The scoped protocol is a restricted CDP subset, not a transparent browser-level
  tunnel. Browser-global mutations/context creation, opaque nested Target envelopes,
  and unknown sessions are refused. Nested iframe, service/shared-worker and other
  child sessions reported on an owned parent inherit that parent's revocation.
  Paused child attachment events are forwarded so clients can resume them.
  `IO` streams, browser-wide download policy, `Storage` and `ServiceWorker` domains
  remain unsupported rather than exposing unscoped browser resources. Each scoped
  command has a 60-second upstream limit, independent of the longer tool request.
  Broader browser-use/Playwright compatibility must be tested before deployment; clients needing unsupported
  browser-level methods may fail. Shared profile cookies are not isolated.
- Before launch, even a non-CDP occupant of the upstream port is refused. After
  launch, the exact child must own the loopback listener (Linux socket inode/PID,
  macOS `lsof` PID/listener verification); unavailable verification fails closed.
  Readiness cannot combine a newly launched PID with another Chrome's WebSocket.
  Scoped reconnect/discovery also refuses a replacement endpoint after browser death.
  A surviving browser is reconciled only when its recorded PID/start identity,
  dedicated profile, listener ownership and browser-instance WebSocket all match. A fresh supervisor
  reloads the same generation and applies one bounded orphan grace, without renewing
  it on repeated crashes. Unknown existing browsers still require operator recovery.
  A new Chrome process gets a new generation and stale scoped sockets close.
- Core registers worker PID and the capability-specific harness socket directory.
  Dead workers get `orphan_s` grace, while unexpired request/download/handoff deadlines
  remain protected. Harness cleanup verifies a private capability-named IPC socket,
  owned page (or an empty/disconnected CDP daemon) and process creation identity before issuing its shutdown protocol;
  it never kills by process-name matching. Unverifiable endpoints are preserved.
  Scoped disposal shuts down both socket directions, fails pending commands and
  joins the upstream reader with a two-second bound (never self-joins).
- Quiet-but-alive tasks expire after `idle_lease_s` without browser use. Observer
  polling and open sockets do not renew them. Their next request gets a new capability;
  old unprotected tabs may already have been reaped. Use an explicit bounded handoff
  before waiting for a human; background liveness is not activity.
- The local user can bypass scopes via the internal CDP port or private files.
  Capabilities prevent accidental cross-owner lifecycle operations, not a hostile
  process already running as that user.

## Tests

From `scripts/`:

```sh
python3 -m unittest -v test_chrome_agent_lifecycle test_hermes_chrome_agent test_chrome_lifecycle_rework test_chrome_supervisor_rework test_chrome_daemon_rework
python3 -m unittest -v test_chrome_astra_rework test_chrome_clock_rework test_chrome_daemon_processes
python3 -m unittest -v test_chrome_endpoint_ownership test_chrome_stop_ownership
```

The lifecycle tests cover ledger isolation/persistence/permissions, corrupt state,
long requests, release/reacquire tokens, bounded handoff/download protection,
concurrent wake, unknown-target shutdown blocking, scoped discovery and WebSockets
against a loopback fake Chrome, owned reconnect auto-attach, forbidden target/session
operations, install-without-start, launchd plist semantics and smoke safety guards.
The existing suite exercises the original focus proxy, guard and passkey paths.
Mac launchd process behavior is mocked, not an on-host acceptance test.
The additional suites exercise bound listeners, real quiet sockets and pending
reader reclamation, deterministic forward/backward clocks and reloads, private
Unix IPC processes, foreign/sibling preservation and ignored shutdown ACKs. The
last command requires Linux Chrome and browser-harness: it launches disposable
profiles on ephemeral ports, tests occupied ports and the actual launch race,
exact-child recovery, and supervisor-only stop with separately spawned harnesses,
including the actual 30-second protected drain bound. Fixtures clean only their
own recorded children. These Linux results do not verify the macOS OS adapters.

For a separately approved real-CDP smoke, the harness launches only its own fresh
headless Chrome/profile and ephemeral ports, refuses ports 9333/9334 and existing
profiles, and cleans up its child/proxy in `finally`:

```sh
python3 smoke_chrome_lifecycle.py --chrome '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
```

It verifies acquire/create/attach/evaluate/list/complete/release, concurrent dialog
handling on the same and a second socket, long evaluation without blocking another
owner or supervisor tick, owned popup use/reaping, a real streamed download whose tab
closes, repeated bounded show, and surviving-Chrome reconciliation by fresh supervisor/
ledger objects. It then verifies idle process/CDP exit and request-triggered wake.
The reconciliation test does not exercise launchd process groups or Mac vdisplay.
`--test-idle-seconds 1` (range 1..10) bounds the test; release grace is one second.
`--headful` opts into a visible isolated browser for Mac testing. It does **not**
prove virtual-display placement, launchd, passkeys, focus guard, or full browser-use
compatibility. Do not run it against the installed supervisor.

## Core routing and rollback

Set `browser.cdp_url` and `browser.managed_agent.endpoint` to the same exact
loopback HTTP root; set `browser.managed_agent.installation_id` to the installed
identity. Core reads these settings per call from the served profile's config.yaml
(use `hermes config set` for the registered keys). Both default and `local=true`
calls acquire before discovery, use the scoped endpoint and owner-specific daemon,
and complete their request in `finally`. A missing tool task identity fails closed.
The lease owner is profile home + task + supplied session, with `default` for an
omitted session. Personal-copy/cloud and unrelated explicit overrides are unchanged.
Task cleanup releases only that profile/task's recorded capabilities. Process exit
and explicit gateway shutdown release all recorded capabilities; a legacy browser
route reset does not revoke managed sibling sessions. Failed releases are retained
for retry on subsequent cleanup/owner reuse, with supervisor process-identity/grace
cleanup as the crashed-worker fallback. Capability rotation retires the old harness
and vault supervisor only after that owner's in-flight requests finish.

Vault keeps its existing no-session API: it addresses the task's last selected
managed session, never a union of capabilities. Alternate sessions using
`browser_exec(session=...)` before vault actions. Each capability has its own
supervisor; failures never fall back to an unscoped browser. Explicit gateway
shutdown remains a release boundary: release immediately invalidates the old
capability. Even a fast shutdown/restart gets a fresh capability and does not
automatically reclaim still-open old tabs; physical closure follows the old
grace/protection bounds. Soft cache eviction is different and remains resumable.
Gateway shutdown stops recorded harness daemons rather than leaving them behind.
Exact-browser supervisor crash recovery is separate from this gateway policy.

To roll back: explicitly stop the supervisor, restore the prior supervisor build,
and remove the managed_agent config mapping. Preserve the Chrome profile. Do not
activate a raw always-on supervisor unless the operator intends that rollback;
removing lifecycle-config.json switches to the legacy always-on behavior. Keep the
ledger for diagnosis rather than deleting it to force reclamation of unknown tabs.
