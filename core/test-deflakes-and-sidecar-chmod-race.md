# Test deflakes (browser, local models, gateway, desktop) and a state sidecar-chmod fix

Patches: 1214 1217 1223 1224 1225 1226 1227 1229 1230 1233 1235

- `patches/1214-test-browser-wait-for-stand-in-browsers-to-finish-ex.patch`
- `patches/1217-test-local-synchronize-pause-resume-transfer-fixture.patch`
- `patches/1223-test-desktop-send-then-close-pays-its-cold-import-at.patch`
- `patches/1224-test-desktop-send-then-close-guards-late-resume-snap.patch`
- `patches/1225-test-desktop-send-then-close-guard-case-reads-storag.patch`
- `patches/1226-test-gateway-deflake-compression-failure-turn-helper.patch`
- `patches/1227-test-desktop-transform-preference-store-graph-at-col.patch`
- `patches/1229-fix-state-tolerate-sidecar-unlinked-between-lstat-an.patch`
- `patches/1230-test-state-hold-both-openers-open-until-both-finish-.patch`
- `patches/1233-tests-give-the-local-recovery-rpc-helper-a-hang-boun.patch`
- `patches/1235-test-kanban-exercise-bytecode-churn-at-plugin-record.patch`

Ten patches only edit tests or test fixtures. 1229 is the one production change (`hermes_state.py`); 1230 is its test follow-up.

## Behavior

Browser / local / gateway tests (Python):
- 1214: stand-in browser children in `test_browser_real_profile.py` signal readiness before the argv scan. `Popen` returns at `execve`, when `/proc/<pid>/cmdline` can still be empty, and `_pid_naming_dir` fails closed on an empty argv. The commit reports 112 of 1000 immediate reads empty.
- 1217: the pause/resume download tests no longer depend on a server-side delay. The first range reader is held after its first real write until the test pauses. The server's per-chunk delay is set to 0, and deadlines are 60 s on a monotonic clock.
- 1226: the compression-failure turn helper's `asyncio.wait_for` goes from 2 s to 120 s (a hang guard only). A quiet run takes about 1.8 s.
- 1233: the local-recovery probe's rpc helper deadline goes from 20 s to 120 s.

Desktop (vitest):
- 1223/1224/1225 (send-then-close test file): the cold module-graph transform is paid at collection, outside any case timeout. A timed-out case can no longer touch the next case: a Vitest timeout does not cancel the body, and the suspended body used to resume inside later cases.
- 1227: the preference-store tests (`guidance-sync`, `sync-defaults`, `sync-handoff`) transform their store graph at collection, not in the first test or a `beforeAll`.

Kanban (Python):
- 1235: a plugin-record reuse test adds a `__pycache__` directory to a plugin dir and bumps the dir mtime, as a concurrent importer would, and asserts the record is still reused (`discoveries == 0`).

State:
- 1229: `_secure_state_db_files` ignores `FileNotFoundError` from `os.chmod`. A sibling connection's last `close()` can unlink `-wal`/`-shm` between the `lstat` and the `chmod`.
- 1230: the quarantine-race test keeps both openers open until both have opened, so a `close()` cannot race the other opener.

## How it works

- 1214: the `stand_in_browser` fixture spawns the base interpreter (`sys._base_executable`, so Windows venv launchers do not re-spawn) running `touch(argv[1]); sys.stdin.read()`. It `wait_until(ready.exists, timeout=120)` using `tests.waiting.wait_until`. An `ExitStack` kills the children at teardown. The child lives until stdin EOF, replacing `sleep(60)`.
- 1217: the `pause_at_progress` fixture wraps `Download._read_range`. The first `Download` instance's `record` callback blocks on `self._paused.wait(60)` after the first write. Pausing sets that Event. `_CHUNK` is 128 KiB. Teardown pauses every held download. `_pause_when_flowing` waits for `done_bytes > 0`, pauses once, then asserts status `paused` with `0 < done_bytes < total_bytes`.
- 1223: `scenario(name, body, timeout)` runs each case under an `AsyncLocalStorage<AbortSignal>` holding Vitest's per-case `signal`. `stillRunning()` throws once that signal is aborted. It is called after `openPage`'s dynamic imports, in `closeWindow`, `saveDraft` and the handle callbacks. A top-level `await Promise.all([import(...)...])` followed by `vi.resetModules()` warms the transform cache and still gives the first page a fresh graph.
- 1224: the `lateSnapshot(pending)` helper does the dynamic import, then `stillRunning()`, then `act(reconcilePendingSubmissions(...))`. It replaces two inline reconciliations. The guard case becomes a `scenario`.
- 1225: the guard case reads storage through `Storage.key(i)`. Spreading `localStorage` is unreliable in the test environment.
- 1227: each file does a top-level `await Promise.all([import(...)])` of every store it loads, then `vi.resetModules()` plus `localStorage.clear()` and `sessionStorage.clear()`.
- 1230: worker 1 sets `late_done` after opening. Worker 0 waits on it (20 s cap) before `close()`.

## Code

`hermes_state.py` (1229). Everything else is tests: `tests/tools/test_browser_real_profile.py`, `tests/hermes_cli/test_local_models_routes.py`, `tests/gateway/test_compression_failure_session_sync.py`, `tests/gateway/fixtures/local_recovery_probe.py`, `tests/hermes_state/test_zeroed_db_quarantine_race.py`, `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.send-then-close.test.tsx`, `apps/desktop/src/lib/ui-preferences/{guidance-sync,sync-defaults,sync-handoff}.test.ts`.

## Tests

- 1229: new `test_secure_files_tolerates_sidecar_unlinked_between_lstat_and_chmod` in `test_zeroed_db_quarantine_race.py`. It patches `hs.os.chmod` to unlink the `-wal` file first, then asserts no exception and that the file is gone.
- 1224/1225: the guard case `a body resumed after its case timed out never touches the next case` asserts that stale `openPage`, `saveDraft`, `closeWindow` and `lateSnapshot` reject with `this case already ended` and leave storage unchanged. The commits state it is red with the check removed.
- 1223 adds the guard case (it is a `scenario`). The commit states a forced-suspension cascade before the fix and none after, and 20 stressed runs passing.
- 1217 adds assertions to `test_download_resume_completes_bytes` (range requests grow after resume). It also applies the fixture to `test_repeated_resume_never_spawns_concurrent_writers` and `test_quickstart_pause_stops_the_sequence`.
- 1235 extends `test_new_plugin_dir_and_config_edit_invalidate_the_record` in `tests/hermes_cli/test_kanban_capabilities_plugin_snapshot.py` with the bytecode-churn step; the test only covers reuse, and no production change ships in the patch.
- 1214, 1226, 1227, 1230 and 1233 are deflakes of existing tests with no new case. Commit messages cite CI or starvation runs. None were re-run for this mirror.

## Applying

Apply the whole numbered series at the merge-base with `git am -3 --empty=keep`. The patches are not independent. 1223, 1224 and 1225 edit the same send-then-close test file in order. 1229 and 1230 edit the same state test file. See `README.md`.

## Gotchas

- A Vitest timeout does not cancel the case body. Any helper that touches shared page or storage state needs an explicit per-case liveness check.
- `vi.resetModules()` keeps transformed code and only discards evaluated modules. Clear storage after the warm-up, because the warm-up's load-time writes would otherwise leak into the first test.
- The 120 s and 60 s deadlines are hang bounds. Waits return as soon as the condition holds, so they do not slow passing runs.
- 1229 swallows only `FileNotFoundError`, and only on the chmod. Other errors still raise.
- In 1230, the close-vs-open race is deliberately excluded from the quarantine test and covered by 1229's own test.
- 1217's `__all__ = ["dl_server"]` keeps the imported pytest fixture from being flagged as unused.

## Plugin?

No, core change.
