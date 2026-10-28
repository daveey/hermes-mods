# Two tests pass under a copied-binary venv interpreter on macOS

Patch **0701** (`437b89b73e`). Test harness only. Product installs are unchanged.

## Behavior

`tests/scripts/test_source_driver.py::test_pm_observer_accepts_ready_fixture_and_leaves_failed_fixture_untouched` (7 params) and `tests/hermes_cli/test_desktop_startup_cost.py::test_current_packaged_launch_does_not_require_npm` pass on macOS under any interpreter, including a venv whose `bin/python` is a copied binary with no `psutil` (the checkout's `venv/bin/python`, 3.11). Before, both failed only there; the PM test environment (a venv whose python is a symlink, with `psutil`) was already green.

## How it works

- The source-driver fixture builds a fake runtime store whose `python/bin/python3` linked to `sys.executable`. A copied venv binary finds its stdlib only through the `pyvenv.cfg` next to it, so the linked copy started with `No module named 'encodings'` and the fixture launcher's `--print-runtime-command` exited 1. The fixture now links the realpath of `sys._base_executable` (falling back to `sys.executable` when that file is missing), the self-contained interpreter the venv was made from.
- The packaged-launch test mocks `subprocess.run` on the shared module, so it also sees `update_lock.process_create_time`'s macOS fallback (`ps -o lstart=`, used when `psutil` is not importable). `assert_called_once` saw 2 calls. The test now asserts exactly one launch of the packaged executable and no `npm`/`npx`/`node` call.

## Code

`tests/scripts/test_source_driver.py`, `tests/hermes_cli/test_desktop_startup_cost.py` only.

## Tests

Both files, on macOS, under `~/.hermes/hermes-agent/venv/bin/python` (3.11, copied binary, no psutil) and the PM test environment (3.14): 11 passed, 1 skipped (PowerShell) each. Before the patch, the same command under the 3.11 venv: 8 failed. Linux runs `/proc` for start times and a symlinked venv, so the fixture links the same binary it did before.

No product entry path changed, so none is claimed.

## Applying

`git am -3` 0701. Test files only.
