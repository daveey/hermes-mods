# Orphan reaping requires proven home and process identity

Patch: **0286**, source `4982888f5d430f8e5e966d643a2e88cc662905c5`.

## Behavior

The Desktop-local backend orphan reaper requires positive ownership by the current real/effective UID and an explicit absolute `HERMES_HOME` matching the current install. Missing or unreadable identity information fails closed. SSH token/owner markers exclude a backend from this reaper; SSH ownership remains with its own watchdog.

Profile flags are resolved before comparing homes. Existing topology, age and backend-lock checks remain required. The process start fingerprint and home ownership are rechecked before TERM and KILL, protecting against PID reuse. Descendants are signalled only when their root was successfully killed and their own identity still matches.

## How it works

`_reap_candidate_in_current_home` checks UID, command-line SSH markers and exec-time home, using the profile-aware home resolver when needed. `_reap_identity_still_matches` combines a fresh ownership check with the start-time fingerprint. The reaper snapshots identities before selecting candidates and checks them again at signal boundaries.

## Code

- `hermes_cli/dashboard_procs.py`
- `tests/hermes_cli/test_orphan_desktop_serve_reap.py`
- `tests/hermes_cli/test_orphan_reap_home_scope.py`

## Tests

On Linux, against the complete applied public series in a standalone verification clone:

```sh
scripts/run_tests.sh -j 1 tests/hermes_cli/test_orphan_desktop_serve_reap.py tests/hermes_cli/test_orphan_reap_home_scope.py
```

Result: **26 passed, 0 failed**. Tests cover topology/locks, foreign/missing/relative/unreadable homes, UID mismatch/access denial, SSH markers, profile overrides, recycled roots and foreign descendants. A real disposable subprocess with a foreign home survives the isolated reaper; discovery is restricted to that child, not live host processes.

The complete **281-patch** series applied with `git am -3` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, exported against upstream `5b32cfc230078417fc523e12b9aabbc9b59f684b` and fork `4982888f5d430f8e5e966d643a2e88cc662905c5`. This verifies kernel/helper behavior, not a live Desktop boot, macOS execution or deployment.

## Applying

Apply the full ordered series from its merge-base; this builds on earlier orphan-reaper and process-identity patches. Existing privacy scrubs and the earlier merge adaptation are retained.

## Gotchas

Old launchers without an explicit absolute exec-time home are deliberately left for their owner to clean up. Platforms without POSIX UID APIs fail closed. Reaping is not expanded to SSH-owned backends. This mirror does not restart any runtime.

## Plugin?

Core process-lifecycle hardening; no plugin is required.
