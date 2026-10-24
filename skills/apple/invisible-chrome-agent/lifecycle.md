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
- Managed raw `/json/new`, `/json/close/*` and `/devtools/*` are refused without
  waking Chrome. Ordinary raw tools cannot create unowned tabs through this port.
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
children are terminated after that bound; startup and process teardown have their
own bounded waits. A disabled installation is never automatically enabled.
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
- A surviving browser is reconciled only when its recorded PID/start identity,
  dedicated profile and browser-instance WebSocket all match. A fresh supervisor
  reloads the same generation and applies one bounded orphan grace, without renewing
  it on repeated crashes. Unknown existing browsers still require operator recovery.
  A new Chrome process gets a new generation and stale scoped sockets close.
- Core registers worker PID and the capability-specific harness socket directory.
  Dead workers get `orphan_s` grace, while unexpired request/download/handoff deadlines
  remain protected. Harness cleanup verifies a private capability-named IPC socket,
  owned page (or an empty/disconnected CDP daemon) and process creation identity before issuing its shutdown protocol;
  it never kills by process-name matching. Unverifiable endpoints are preserved.
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
```

The lifecycle tests cover ledger isolation/persistence/permissions, corrupt state,
long requests, release/reacquire tokens, bounded handoff/download protection,
concurrent wake, unknown-target shutdown blocking, scoped discovery and WebSockets
against a loopback fake Chrome, owned reconnect auto-attach, forbidden target/session
operations, install-without-start, launchd plist semantics and smoke safety guards.
The existing suite exercises the original focus proxy, guard and passkey paths.
Mac launchd process behavior is mocked, not an on-host acceptance test.

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
shutdown remains a release boundary (tabs are not guaranteed across a long gateway
restart); it stops recorded harness daemons rather than leaving them behind.

To roll back: explicitly stop the supervisor, restore the prior supervisor build,
and remove the managed_agent config mapping. Preserve the Chrome profile. Do not
activate a raw always-on supervisor unless the operator intends that rollback;
removing lifecycle-config.json switches to the legacy always-on behavior. Keep the
ledger for diagnosis rather than deleting it to force reclamation of unknown tabs.
