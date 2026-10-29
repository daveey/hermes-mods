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

Proposed appended patches **0375, 0376, 0377**, after the preserved published series
through 0362 (including profile-scoped GitHub broker patch 0335):

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
Apply in order after 0362; do not renumber existing siblings. The full mirror uses
the fork/upstream merge-base, not the newer upstream tip. Existing public privacy
substitutions and the merge-adapted patch 0251 must be preserved. The documentation
correction verification applied all 266 patches with `git am -3 --empty=keep` from merge-base
`4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63` in a disposable clone; all ten
feature-path blobs matched core head `6add72d407e8cc47fabc01b1f2a468828f4c1578`.
Patches 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148, 0149, 0150, 0151, 0152, 0153, 0154, 0155, 0156, 0157, 0158, 0159, 0160, 0161, 0162, 0163, 0164, 0165, 0166, 0167, 0168, 0169, 0170, 0171, 0172, 0173, 0174, 0175, 0176, 0177, 0178, 0179, 0180, 0181, 0182, 0183, 0184, 0185, 0186, 0187, 0188, 0189, 0190, 0191, 0192, 0193, 0194, 0195, 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207, 0208, 0209, 0210, 0211, 0212, 0213, 0214, 0215, 0216, 0217, 0218, 0219, 0220, 0221, 0222, 0223, 0224, 0225, 0226, 0227, 0228, 0229, 0230, 0231, 0232, 0233, 0234, 0235, 0236, 0237, 0238, 0239, 0240, 0241, 0242, 0243, 0244, 0245, 0246, 0247, 0248, 0249, 0250, 0251, 0252, 0253, 0254, 0255, 0256, 0257, 0258, 0259, 0260, 0261, 0262, 0263, 0264, 0265, 0266, 0267, 0268, 0269, 0270, 0271, 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286, 0287, 0288, 0289 were
preserved byte-for-byte. This application check is not a full-suite CI result.

The subsequent integration onto fork main `f51c577542` moves only these three
feature patches to 0292, 0293, 0294, 0295, 0296, 0297, 0298. All 267 existing main patches, including privacy
adaptations and 0251, remain byte-for-byte intact. All 270 patches applied with
`git am -3 --empty=keep` from the same merge-base in a disposable clone; all ten feature-path
blobs matched integrated core `b27b88b0dc67c27b8a4724905a6ff3dce13ecbf1`.
Both the 266- and 270-patch receipts above are historical, not the current apply order.

The earlier final pinned-base integration uses fork main `ca145ec36e04a7db3271e2df91622dc7adbd91a2`
and public main `1414cbbb0ca02b30bd7b0159a0a7c1a43a42a4aa`. It preserves all 275
public main patches byte-for-byte, including historical privacy substitutions and
adapted 0251. The already-published topology renumbering is retained, not repeated.
Patch 0312 mirrors the single additional landed Kanban broker commit; these Chrome
patches follow as 0329, 0330, 0331. The complete 279-patch series applied with `git am -3 --empty=keep`
from the same merge-base; all ten Chrome paths and all five broker paths match
integrated core `54716420ff7c58cf1ab34fbfb22ef9e878f87cd3` (the config path overlaps).
The 189 core tests, two documentation-drift tests and 16 broker/acceptance tests
pass on that integrated core. The canonical runner uses the existing PM interpreter
with a scratch-location-only adapter in a disposable clone; no assertions changed.
Browser runtime/helpers are unchanged. The shared configuration module changes only
Kanban defaults/comments, so it does not invalidate historical native evidence or
imply a new native run. Full remote CI and independent exact-delta review remain gates.

The historical recovered-CI integration pins core `39cdabdc623caf3661406446efc4610640b81107`
and published public main `44ec0cd377b6afba7b656107eb29c1e13d3d4b9a`.
All 284 published patches are preserved byte-for-byte; the three unchanged Chrome
commits occupied 0352, 0353, 0354 in that snapshot. The complete 287-patch series applied with `git am -3 --empty=keep`
from the same merge-base. All ten Chrome paths match integrated core
`6d2434b45db1a4f2c0a6206930504cce1c4549db`; the 24 newly integrated sibling paths
also match except the preserved published task-identifier substitution in a Kanban
test docstring (no assertion change). Canonical targeted tests passed 189 core
and two documentation cases using the same PM interpreter/allocation-only adapter.
The 279-patch receipt above is historical. This imports published poll-runner
workflow routing, not a CI waiver: applicable exact-head CI and independent
delta review remain required. Later unpublished mirror work is not duplicated.
No browser/native behavior is changed or new native execution claimed.

The subsequent published-baseline reconciliation pins core
`e295d0da73b18c27e5898ee5ef3411688ba66ef7` and public main
`b25626626475551ffd4a4b7dcd39cbb41048315b`. Its already-published mirror contains
292 patches, including merged commits inserted earlier in the ordered history.
All 292 published patch files are preserved byte-for-byte, including adapted
0251 and the published attribution/test-fixture privacy substitutions. The broker
is now 0335 (0330 in the recovered-CI snapshot); Chrome follows as 0375, 0376, 0377.
All 295 patches applied with `git am -3 --empty=keep` from the same merge-base. The ten Chrome
blobs match integrated core `5b105d0a1ffd01da75885373d3d39a69270abb33`, and feature
application preserves every sibling tree entry from the applied public baseline.
All three Chrome commits remain patch-equivalent to the recovered-CI candidate.
Canonical tests on this new core passed 189 core cases and two documentation
cases; `scripts/check --commit HEAD` passed all twelve checks, using the same PM
interpreter and scratch-allocation-only runner adapter described above.
The previous core CI success belongs to the old exact head, not this rebased
candidate: new exact-head CI and independent changed-delta review are required.
No unpublished sibling backlog is exported or native runtime acceptance implied.

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
