# Background plugin checks never resurrect a retired profile home

Patches **2046** (`cc5090ac4998bdc0bde7b8a00104d7e618f94c1c`, `fix(profiles): prevent plugin checks resurrecting retired homes [daveey]`) and **2047** (`a8cc4432624b2cdefb291f402c0501a24b714c70`, `test: guard retirement regression from unittest discovery [daveey]`).

## Behavior

A named profile home (`<root>/profiles/<name>`) that was renamed, deleted or tombstoned is no longer recreated by the gateway's scheduled plugin-update check or by its PM receipt writes:

- **Retired before the check:** `maybe_run_gateway_check` runs no checks and writes nothing.
- **Retired during the check:** checks may already have run, but no auto-apply happens, and `plugin-update-checks/`, `logs/` and the home itself are not created again. A tombstoned home still exists on disk; it simply gets no new marker or log directories.
- **Live home:** unchanged. Markers (`plugin-update-checks/last-run`, `results.json`) and the receipt (`updates-available`) are written and auto-apply runs.
- `mkdir_under_hermes_home` under a named home creates each missing component one level at a time, never `parents=True` above the home. If the home is renamed between the liveness check and a nested `mkdir`, creation fails with `FileNotFoundError` instead of rebuilding the old path.
- Receipt publication (`pm/receipt._write_rotated`) no longer recreates the receipt directory between its two writes (rotated file and `latest.json`). If the home disappears after either write, finalize returns `None`, the old path stays absent, and the renamed profile is untouched.
- 2047: `tests/test_named_profile_mkdir_retirement.py` raises `ImportError` unless imported as `tests.*`, so plain `unittest` discovery cannot run it outside the test-isolation harness.

## How it works

- `hermes_cli/plugins_cadence.py`: `run_scheduled_check` calls `assert_named_profile_home_live(get_hermes_home())` before `check_due` and returns `None` on `FileNotFoundError`. `_run_check_locked` repeats it after the checks and before `_auto_apply`. `_markers_dir` uses `mkdir_under_hermes_home`. `maybe_run_gateway_check` defaults `plugins_dir` to `get_hermes_home() / "plugins"` instead of `plugins_cmd._plugins_dir()`, a read path that could create the home from a stale served-profile list.
- `hermes_constants.mkdir_under_hermes_home`: after its existing liveness assertion, when `named_profile_home(target)` is not `None` it walks `target.relative_to(home).parts` and calls `make_user_dir(current, exist_ok=True)` per component. Non-profile targets keep `parents=True`.
- `pm/receipt._write_rotated` creates the directory with `mkdir_under_hermes_home`, serializes the JSON payload once, and writes both files with `pm.filesystem.durable_write_bytes(..., create_parent=False)`. That new keyword skips the writer's own `parent.mkdir(parents=True)`.
- PM's stdlib-only bootstrap closure now needs `hermes_dir_acl.py` next to `hermes_constants.py`. The `Dockerfile` copies it, and the staged-closure tests copy it too.

## Code

- `hermes_cli/plugins_cadence.py`, `hermes_constants.py`, `pm/filesystem.py`, `pm/receipt.py`, `Dockerfile`.

## Tests

Listed from the patches. Not rerun in this mirror pass.

- `tests/hermes_cli/test_plugins_cadence_retirement.py` (new): `test_gateway_check_respects_profile_retirement`, parametrized over `when` ∈ {before, during, live} × `retirement` ∈ {rename, delete, tombstone}.
- `tests/pm/test_receipt_profile_retirement.py` (new, POSIX only): `test_receipt_profile_rename_during_publication` (rename at the first or second write).
- `tests/test_named_profile_mkdir_retirement.py` (new; 2047 adds the import guard): `test_named_home_rename_during_child_mkdir` (rename after the first or second component).
- `tests/pm/test_bootstrap_import_closure.py`: the `-S` minimal closure now also runs `receipt.begin`/`finalize`/`latest`.
- `tests/pm/test_activation_setup.py`, `tests/pm/test_cold_runtime_e2e.py`, `tests/pm/test_startup_recovery.py`: staged trees include `hermes_dir_acl.py`.

```sh
scripts/run_tests.sh tests/hermes_cli/test_plugins_cadence_retirement.py tests/pm/test_receipt_profile_retirement.py \
  tests/test_named_profile_mkdir_retirement.py tests/pm/test_bootstrap_import_closure.py
```

Only the gateway scheduled-check entry point (`maybe_run_gateway_check`) is tested for retirement. A manual `hermes plugins` check run against a retired profile is untested here.

## Applying

Apply 2046 and 2047 together through the snapshot-bound replay in [README.md](README.md). Images built from this tree need the `Dockerfile` change. Without `hermes_dir_acl.py` beside `hermes_constants.py`, PM receipt writes fail on import.

## Gotchas

- A tombstoned profile still exists on disk, so tests assert `home.exists()` only for the tombstone case. "Not resurrected" there means no new subdirectories.
- Checks that already ran when retirement happens may have made network calls. Only the apply and the writes are suppressed.
- `durable_write_bytes(create_parent=False)` requires an existing parent. Callers outside receipts still default to creating it.

## Plugin?

No. Profile-home liveness and PM receipt writing are core/bootstrap code that runs before plugins load.
