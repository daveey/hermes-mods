# Mirror 2030 follow-ups: test hardening and a catalog revalidation

Patches **2030, 2040, 2042, 2044, 2045**. All are test-only or a comment-only catalog edit. None changes runtime behavior.

## Behavior

| Patch | Source commit | Change |
| --- | --- | --- |
| 2030 | `f7e054b441100dac6f941b5a8c4fa764193f0bce` | Stall-probe tests: the repeated-`stop()` assertion compares against a captured count rather than the mutable `samples` array. The real write+fsync test rejects its wait after 10 s, before Vitest's 30 s timeout, with `Only N/3 fsync samples completed in 10s`, and its `finally` clears the deadline, awaits `probe.stop()` and then removes the temp directory even if no samples arrived. Follows the 2022 change in [mirror-2029-features.md](mirror-2029-features.md). |
| 2040 | `0565c58a80f3595c15000ef02280ef32192c807c` | `plugin-catalog/yuanbao.yaml` gains a two-line comment; the pinned `sha` is unchanged. Touching the file makes Plugin Catalog CI (path-filtered on `plugin-catalog/**`) re-run admission of the same pin once Yuanbao's core extraction is on the trusted base. Against the pre-extraction core, admission correctly rejects the plugin's `yb_*` names. |
| 2042 | `a0743b91101c46548f264328ea0983a274ecc54e` | Browser-use daemon ownership test: after `Popen`, wait (up to 30 s) until `psutil.Process(pid).cmdline()` is non-empty before publishing the pid, because the exec handshake can finish before argv is visible. Only that precondition is retried, never the ownership verdict under test. A new parameter forces one empty-argv observation to reproduce the race. |
| 2044 | `bdef109f271efac00784ebfa4b337f05a08d15e2` | Hosted-room owed-revoke bound tests use one fixed clock (`1_700_000_000.0`) and pass it to `owe_room_grant_revoke(now=...)` (an existing optional parameter), so creation and due queries agree even if SQLite setup is descheduled. The horizon assertion is now exact (`now + MAX_OWED_REVOKE_SECONDS`) instead of `<= ... + 60`. |
| 2045 | `eaf03c726e7ccb88658eb05043c5db3b768663a4` | Adds a `now=None` default for the shared `_owe` helper's parameter introduced as required in 2044. `test_hosted_room_owed_revoke_cap.py` and `test_hosted_room_owed_revoke_unsigned_claims.py` import `_owe` and call it without `now`. |

## How it works

- 2030: `tests-js/workspace-check-stall-probe.test.mjs` stores `probe`/`deadline` outside the `try`, so `finally` can stop a probe that never produced three samples.
- 2042: `tests/tools/test_browser_use_cli_owned_daemon.py::test_a_daemon_another_live_hermes_started_on_the_same_name_is_never_registered` monkeypatches `psutil.Process.cmdline` for the `unpublished_argv=True` case, then uses `wait_until(...)` on the raw argv.
- 2044/2045: `tests/gateway/test_hosted_room_owed_revoke_bounds.py` removes `import time`. `_owe(db, grant, *, now=None, ...)` forwards `now` to `hosted_rooms.owe_room_grant_revoke`, which resolves `None` to the current time.

## Code

No runtime code. Files: `tests-js/workspace-check-stall-probe.test.mjs`, `plugin-catalog/yuanbao.yaml`, `tests/tools/test_browser_use_cli_owned_daemon.py`, `tests/gateway/test_hosted_room_owed_revoke_bounds.py`.

## Tests

Listed from the patches. Not rerun in this mirror pass.

```sh
pnpm --dir tests-js exec vitest run workspace-check-stall-probe.test.mjs
scripts/run_tests.sh tests/tools/test_browser_use_cli_owned_daemon.py \
  -k test_a_daemon_another_live_hermes_started_on_the_same_name_is_never_registered
scripts/run_tests.sh tests/gateway/test_hosted_room_owed_revoke_bounds.py \
  tests/gateway/test_hosted_room_owed_revoke_cap.py tests/gateway/test_hosted_room_owed_revoke_unsigned_claims.py
```

2040 has no local test. Its check is the `structural` and `pinned-source-validate` jobs of `.github/workflows/plugin-catalog-ci.yml` on a PR touching the file, which this pass did not run.

## Applying

Apply 2044 and 2045 together: 2044 alone breaks the two sibling test modules that call `_owe` without `now`. Use the snapshot-bound replay in [README.md](README.md) for all of these.

## Gotchas

- 2040's comment is the whole change. Revalidation passes only once the trusted base contains Yuanbao's core extraction. Before that, rejection is the correct outcome, not a regression.
- 2042's module is marked `pytest.mark.platforms("posix")`, imports `psutil` and spawns a real stand-in daemon.

## Plugin?

No new plugin. 2040 concerns the catalog entry for an external plugin (Yuanbao), not plugin code in this repository.
