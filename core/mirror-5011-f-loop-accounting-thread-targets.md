# Loop accounting for thread targets and pool submit

Mirror snapshot 5011; ordinals 4876, 4935.

Entries:

- **4876**: loop accounting: name unaccounted thread CPU per Thread target, split user/system per family
- **4935**: fix(accounting): name direct pool submissions without duplicate scopes

## Behavior

- **Thread targets** (4876): unaccounted thread CPU is named per `Thread` target; user/system split per family; nested scopes are not counted twice; lambdas are told apart; the wrap is once and restored; flag-off leaves `Thread.run` alone.
- **Pool submit** (4935): public `ThreadPoolExecutor.submit` is instrumented so idle snapshots name the submitted function, not `concurrent.futures.thread._worker`. Layered thread scopes are idempotent. Context/custody wrappers keep the original callable. Exited native IDs are folded before replacement targets begin so reused IDs do not double-book.

## How it works

- `hermes_cli/loop_accounting.py` (`instrument_thread_targets`, `_targets_residual`, `_callable_target`).
- `hermes_cli/local_work_threads.py` and `tools/daemon_pool.py` keep per-submission context on an inspectable partial.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/local_work_threads.py`
- `hermes_cli/loop_accounting.py`
- `tools/daemon_pool.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_local_work_threads.py`
- `tests/hermes_cli/test_loop_accounting_pool_submit.py`
- `tests/hermes_cli/test_loop_accounting_thread_targets.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Physical-work custody is not CPU accounting. Direct submit hooks must restore the original `submit` when disabled.

## Plugin?

None.
