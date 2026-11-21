# Test fixtures: remove import, PID-alias and receipt-ordering races

Mirror snapshot 5406; ordinals 5337, 5399, 5402.

Entries:

- **5337**: fix(tests): await host-loop lease import during retirement reset
- **5399**: test(kanban): isolate owner-death fixtures from pytest PID aliases
- **5402**: Wait for the complete Bot overflow-refusal receipt

## Behavior

- **5337**: the autouse reset helper reads a lease module from `sys.modules`, which can be published before its background import finishes; it now uses `import_module` to synchronise with the import lock, only when the module was already used.
- **5399**: owner-death fixtures assigned fixed fake PIDs that could alias the test runner's own PID; every synthetic process now defaults to dead and the live-owner control opts in explicitly.
- **5402**: a retry test checked a refusal marker that is not the terminal receipt; it now waits for refusal, failed state and reason before asserting.
- No production behavior changes.

## How it works

No shipped production paths; entries change only test fixtures.

## Code

Shipped non-test paths (union over these entries):

- (none: these entries ship test files only)

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/_fixtures/host_loop_reset.py`
- `tests/hermes_cli/conftest.py`
- `tests/test_conftest_host_loop_reset.py`
- `tests/hermes_cli/test_kanban_owner_death.py`
- `tests/gateway/bot_retry_interleaving.py`
- `tests/gateway/test_session_bot_retry.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- These are coverage hardening; they do not change policy. Run results are producer-reported only.

## Why core instead of a plugin

Test-only; the fixtures exercise core modules and must ship beside them.
