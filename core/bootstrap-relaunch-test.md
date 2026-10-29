# Real-bootstrap relaunch test survives the bootstrap preload

Patch **0683** (`c1267464b7`). Test harness only. Product installs are unchanged.

## Behavior

`test_real_bootstrap_reexecs_before_app_imports` keeps passing after bootstrap is loaded before the entry point.

The test's disposable source root holds a copied `hermes_bootstrap.py` and not the repository `pm/` package. A relaunch that loads that bootstrap first (`from pm.environments import ...`) used to die with `No module named 'pm'`. The copied bootstrap now imports `launch_test_tools` immediately after its `__future__` line, so both the entry load and the relaunch preload see the same repository path and acquisition fakes.

Real installs ship `pm/` in the root, so this failure is the fixture, not the product path.

## How it works

The test rewrites the copied bootstrap to import `launch_test_tools` before any `pm` import. That module is what previously ran only after the entry point imported it, which is too late once relaunch preloads bootstrap.

## Code

`tests/pm/test_source_update_launch.py` only.

## Tests

The existing `test_real_bootstrap_reexecs_before_app_imports` (three params) is the entry path. The commit message reports it passing together with `tests/pm/test_borrowed_home_launch.py` (26 passed). This mirror did not re-run that file (it needs real `uv`).

No product entry path changed, so none is claimed.

## Applying

`git am -3` 0683. Test file only; an empty apply on a tree that already has the import is a no-op, not a product change.

## Gotchas

- Do not "fix" this by vendoring `pm/` into the disposable root. The point of the fixture is a copied bootstrap plus the test's path/fakes.
- A host without `uv` still errors in this file's fixtures. That is a setup error, not this assertion failing. See [test-run-summary-errors.md](test-run-summary-errors.md).

## plugin?

No. Test-only.
