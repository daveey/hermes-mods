# Managed Chrome leases (opt-in, experimental)

This extends the existing focus proxy; it does **not** replace the passkey notifier,
focus guard, virtual display, Chrome arguments, or saver preferences. Existing
installations without `cache/chrome-agent/lifecycle-config.json` retain always-on
raw CDP behavior. Reinstalling writes that file and opts into demand startup.

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
  diff inference or adoption. Invalid scoped requests never fall back to raw CDP.
- Raw `/json/*` and WebSockets keep the existing focus filter and remain unowned.
  Unknown targets block browser idle shutdown and are never automatically closed.
- Owned expired tabs are closed through CDP, not by killing renderers. Defaults:
  lease idle 1800 seconds, release/orphan grace 300, browser idle 120, handoff 1200.
  Each request protects through `timeout_s + 30` seconds, including after ledger reload.
- The version-1 ledger uses a process/thread lock, atomic replace, fsync, and mode
  0600. Browser generations invalidate bindings. Invalid ledgers fail closed;
  they are not replaced with an empty ledger. Only one supervisor holds the lifetime lock.
- Human show/passkey reveals protect known owned targets with a bounded persisted
  deadline. Downloads are tracked by GUID; increasing bytes refresh a 120-second
  stall deadline, capped at 3600 seconds from first observation. Progress never
  refreshes the lease itself. Persisted deadlines survive reload.
- The listener remains alive after idle shutdown. Concurrent acquires serialize
  wake. A deliberately stopped/disabled launchd job is not auto-bootstrapped.

## Install / configure

On a Mac, with explicit operator approval:

```sh
./scripts/hermes-chrome-agent install --no-start
# Builds helpers and writes the job/config, but leaves the supervisor unloaded.
./scripts/hermes-chrome-agent activate
./scripts/hermes-chrome-agent stop
./scripts/hermes-chrome-agent disable
```

`install` without `--no-start` also activates. `activate` is explicit and re-enables
an operator-disabled job. `stop` unloads; `disable` disables then unloads. Launchd
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

Install generates the identity once; reinstall preserves it. Settings are finite
numbers between 1 and 86400 seconds; booleans and unknown keys are rejected.
Changing settings requires a deliberate supervisor restart, which closes its children.
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

**Not production-verified. No real Chrome or browser-use run was performed for this change.**

- Chrome's unchanged launch arguments include an initial `about:blank` tab. It is
  unknown and therefore intentionally blocks automatic idle shutdown until an
  operator closes it. This implementation does not silently adopt/delete it.
- The scoped protocol is a restricted CDP subset, not a transparent browser-level
  tunnel. Browser-global mutations/context creation, nested Target envelopes,
  popup/worker adoption, and unknown sessions are refused. Browser-use/Playwright
  compatibility must be tested before deployment; clients needing unsupported
  browser-level methods may fail. Shared profile cookies are not isolated.
- Frame-associated download events need the supervisor's Page observation to have
  identified the frame. A download that precedes that observation may not be bound.
  Real Chrome event coverage remains unverified. A legacy client can also change
  browser-wide download behavior; the scoped path refuses that command.
- A supervisor restart with a still-running profile browser refuses wake rather
  than adopting or killing it. An operator must recover that browser explicitly.
  Generation reset occurs only when starting a new child browser.
- A handoff deadline is not renewed by repeated reveal/polling of the same target.
  There is no separate human "extend handoff" API in this version.
- The local user can bypass scopes via raw CDP, the internal port, or private files.
  Capabilities prevent accidental cross-owner lifecycle operations, not a hostile
  process already running as that user.

## Tests

From `scripts/`:

```sh
python3 -m unittest -v test_chrome_agent_lifecycle test_hermes_chrome_agent
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

It verifies acquire/create/attach/evaluate/list/close/complete/release, **not** the
virtual display, launchd, passkeys, full browser-use execution, or startup-tab idle
acceptance. Do not run it against the installed supervisor.
