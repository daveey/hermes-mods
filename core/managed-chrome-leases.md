# Managed Chrome owner leases (experimental)

## Behavior

An explicit `browser.managed_agent` opt-in gives each profile/task/named session
its own scoped CDP capability and browser-harness daemon. Default and `local=true`
`browser_exec` acquire before discovery, complete each request in `finally`, and
never infer management from a familiar port. Unconfigured routes remain unchanged.

Task cleanup releases only that profile/task. Explicit gateway shutdown and
process exit release recorded capabilities; a legacy per-chat route reset does
not revoke siblings. Vault's existing no-session API follows the last selected
managed session, never a union of scopes or an unscoped fallback.

This is publication preparation, not deployment or complete native acceptance.

## How it works

The core matches the resolved endpoint against the operator's exact loopback
endpoint and installation identity. An owner is profile home, task identity and
tagged named session (or the omitted-session default). Per-owner locks isolate
network I/O; request tokens prevent late completion affecting a newer request.
Capability rotation defers old daemon/supervisor disposal until in-flight work
finishes. Failed cleanup remains retryable.

The companion [Chrome supervisor lifecycle](../skills/apple/invisible-chrome-agent/lifecycle.md)
owns durable leases, target attribution, monotonic protection deadlines, verified
process/IPC retirement and demand-driven browser/display wake. Idle listener
availability is distinct from an explicitly disabled installation: core does not
bootstrap a stopped launchd job or execute an inferred wake helper.

## Code

Proposed appended patches **0264–0266**, after the existing series through 0263:

- `tools/browser_agent_lifecycle.py`: route validation, acquire/complete/release,
  owner-specific daemon and vault identity, bounded cleanup retry.
- `tools/browser_use_cli.py`: default/local/named dispatch and scoped supervisor.
- `tools/browser_vault_tool.py`: selected-owner lookup with no unscoped fallback.
- `tools/browser_tool_lifecycle.py`, `gateway/run_shutdown.py`: task cleanup versus
  explicit process-shutdown release boundary.
- `hermes_cli/config_defaults.py`: disabled-by-default managed-agent mapping.
- `website/docs/user-guide/configuration.md`: explicit opt-in, empty defaults,
  exact endpoint matching and operator activation for a stopped installation.

## Tests

Linux checks on the integrated core head before the documentation-only correction
passed (runtime files are unchanged):

- `scripts/run_tests.sh tests/tools/test_browser_agent_rework.py tests/tools/test_browser_agent_lifecycle.py tests/tools/test_browser_use_cli.py tests/tools/test_browser_vault.py tests/tools/test_browser_supervisor_reconnect.py tests/gateway/test_shutdown_kill_off_loop.py tests/gateway/test_gateway_shutdown.py`:
  189 passed across seven files, zero failures.
- `scripts/check --commit HEAD`: all twelve checks passed. Current main already
  contains the portable wake-probe termination fix; no lint waiver was added.
- Isolated actual core/harness smoke: default, local and named entries, three
  sibling daemons, crashed-worker retirement with siblings preserved, stale-browser
  generation recovery, selected vault evaluation/reconnect, release-all retirement
  and immediate reuse with a fresh capability. Final owned daemon count was zero.
  This task-local integration probe exercises real core Python entry points and
  browser-harness, not an end-to-end CLI chat, Desktop UI or live gateway.
- Companion public suite: 151 tests passed with zero Linux skips, including real
  harness retirement and bounded stop. A separate native public run reported 151
  tests OK with eleven Linux-only skips; that is uploaded evidence, not local execution.

For the configuration-documentation correction, the canonical
`scripts/run_tests.sh tests/ci/test_docs_drift.py` reproduced one failure on the
two missing managed-agent keys before the edit, then passed both tests after it.
No baseline or test was changed.

The vault smoke evaluates harmless markers through the actual selected-owner API;
it does not fill genuine credentials. Native core runtime remains untested.
Uploaded native production-supervisor evidence covers generated launchd plist,
existing helpers, disabled-bootstrap refusal, headful virtual-display placement,
scoped evaluation/focus containment with unchanged loginwindow foreground on a
locked screen, idle browser/display exit, next wake and explicit OFF. Unlocked
typing was not tested. A separate isolated native `install --managed --no-start`
run compiled the Swift helpers/notifier and verified owner-only configuration,
disabled/unloaded state, refused bootstrap and no browser creation; cleanup
restored the original notifier registration. Real login/reboot persistence,
genuine Keychain/Touch ID/passkeys, notification/handoff and broad consumer
compatibility remain unexercised, not claimed. Native core repetition and these
coverage limitations are not additional mandatory gates for this lifecycle change.
See the lifecycle acceptance section for the failed-first-probe qualification.

## Applying

These three patches are feature-branch proposals, not a released installation.
Apply in order after 0263; do not renumber existing siblings. The full mirror uses
the fork/upstream merge-base, not the newer upstream tip. Existing public privacy
substitutions and the merge-adapted patch 0242 must be preserved. The documentation
correction verification applied all 266 patches with `git am -3` from merge-base
`4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63` in a disposable clone; all ten
feature-path blobs matched core head `6add72d407e8cc47fabc01b1f2a468828f4c1578`.
Patches 0001–0265 were
preserved byte-for-byte. This application check is not a full-suite CI result.

Keep the supervisor OFF until the identified integration/release blockers are
closed and the operator explicitly authorizes deployment/activation. This remains
experimental and not release-approved: unsuccessful/incomplete exact-head CI and
independent changed-head review remain blockers. The configuration-documentation
ratchet is corrected locally; CI and independent review must verify the new heads.
Configure only an explicitly approved dedicated installation. Never attach to or
retire an unrelated browser. No deployment command is authorized by these docs.

## Gotchas

- The scoped CDP subset refuses browser-global mutations, unknown targets and
  unsupported IO/Storage/ServiceWorker methods. Commands have a 60-second upstream
  limit; a longer tool request timeout does not widen it. Shared cookies are not
  a hostile same-user isolation boundary.
- The harness runtime path must fit the full capability socket pathname. Core
  sends its actual configured path; the supervisor rejects oversized paths before wake.
- A stopped installation requires explicit operator activation. A listener-only
  idle installation can wake on an authorized acquire.
- Soft eviction is resumable; explicit shutdown invalidates capabilities even
  during a quick restart. Old protected tabs are not automatically adopted.
- Fixture mock-Keychain success is not genuine credential acceptance. Production
  launch must remain free of that test-only flag.

## Plugin?

The supervisor lives in the public skill; only core route selection, task cleanup,
shutdown and vault ownership require these narrow core changes. No new core tool
or repository policy is introduced.
