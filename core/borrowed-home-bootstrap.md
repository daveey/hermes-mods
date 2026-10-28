# Activate dependencies before borrowed-home user code

Patch 0655 (`bcf47021db`).

## Behavior

A borrowed/temporary HERMES_HOME relaunch onto the store interpreter activates this checkout's dependencies before running user code. It does not re-exec when already on the store interpreter.

## How it works

The isolated `python -I` run-path trampoline loads `hermes_bootstrap` before user `run_path`, rather than assuming the launching virtualenv's site-packages survived isolation. Interpreter identity prevents a redundant relaunch.

## Code

`hermes_cli/venv_sync.py`.

## Tests

`tests/hermes_cli/test_borrowed_home_launch.py`: **7 passed** with the canonical runner on the applied public patch. This is the regression suite for borrowed-home launch and bootstrap ordering; no live interactive Desktop launch is claimed.

## Applying

Applied with `git am -3` in a disposable worktree at `599752f5c9`; removed after the tests.

## Gotchas

The trampoline still needs a valid checkout bootstrap and packaged runtime; this is not a runtime reinstall mechanism.

## plugin?

No; dependency activation happens before plugins and user code run.
