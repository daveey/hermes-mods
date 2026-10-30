# CI lanes and test deflakes, 0961-1050

Patches:

- `patches/0961-test-tui_gateway-wait-for-delivered-before-consumer-.patch`
- `patches/0964-test-e2e-the-dashboard-concurrent-writer-commits-at-.patch`
- `patches/0965-fix-compression-wait-out-the-deterministic-fallback-.patch`
- `patches/0966-fix-kanban-pin-utf-8-decoding-on-host-reboot-journal.patch`
- `patches/0968-ci-gate-the-fork-disabled-tests-os-lane-is-a-tolerat.patch`
- `patches/0970-test-match-completion-events-by-delegation_id-in-gro.patch`
- `patches/0973-test-cron-synchronize-bounded-cleanup-assertions-dav.patch`
- `patches/0975-test-desktop-archive-preflight-resolves-a-hub-chat-p.patch`
- `patches/0978-test-desktop-expose-late-portal-fixture-stalls-befor.patch`
- `patches/0979-ci-hermes-state-macos-utility-QoS-and-2-workers-on-t.patch`
- `patches/0980-test-keep-reused-pid-fixture-outside-our-creation-se.patch`
- `patches/0997-tests-deflake-termux-assembly-permissions-test-gener.patch`
- `patches/0998-ci-run-termux-assembly-permissions-on-docker-ci.patch`
- `patches/0999-ci-put-termux-assembly-mounts-where-the-docker-ci-da.patch`
- `patches/1000-ci-diagnose-docker-ci-bind-mount-visibility-temporar.patch`
- `patches/1001-ci-diagnose-assembly-dir-visibility-v2-temporary.patch`
- `patches/1002-fix-ci-preserve-Docker-shared-mount-root-through-tes.patch`
- `patches/1013-test-tui_gateway-deflake-hosted-room-service-waits-2.patch`
- `patches/1016-test-cron-synchronize-terminal-fire-fence-revocation.patch`

Housekeeping commits that keep the fork's CI green. They change workflows, runner scripts or tests, plus two small source fixes.

## Behavior

- 965 (source fix): the deterministic compression fallback waits out its local work instead of inheriting the LLM idle window.
- 966 (source fix): host-reboot `journalctl` reader pins utf-8.
- 968: release gate tolerates fork-disabled jobs (`tests-os`); `fork_guard` tests are never converted to skips.
- 979: the macOS lane runs at utility QoS with 2 workers. 985-990: termux assembly permissions run on a `docker-ci` runner and fail the lane on any skip (988-989 were temporary diagnostics, superseded by 990, which preserves the Docker shared mount root in `run_tests.sh`).
- Test-only: 961, 964, 970, 973, 975, 978, 980, 1013, 1016 (wait on events or deadlines instead of fixed sleeps, stable fixtures).

## How it works

No runtime behavior beyond 965 and 966.

## Code

`agent/conversation_compression.py`, `hermes_cli/kanban_host_reboot.py`, `scripts/ci/required_results.py`, `scripts/fork-ci-runner.sh`, `scripts/run_tests.sh`, `.github/workflows/*.yml`, `tests/`.

## Tests

`test_compression_stall_deterministic_fallback.py`, `tests/ci/test_required_results.py`, `tests/ci/test_fast_runner_routing.py`, `tests/scripts/test_run_tests_shell.py`, `tests/scripts/test_termux_assembly_permissions.py`; the rest are the deflaked tests themselves. The workflow changes are untested here.

## Applying

CI-only: the workflow patches are meaningless on a host without these runners.

## Gotchas

None known beyond the notes above.

## Plugin

None.
