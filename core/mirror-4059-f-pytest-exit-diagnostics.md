# Targeted late pytest exit diagnostics

Mirror batch 6; ordinals 4056.

## Behavior

The runner captures late shutdown stacks for the derived-session test investigation. This is diagnostics, not a deflake or an identified root-cause repair.

## How it works

A plugin loaded only for the affected file duplicates original stderr before capture, emits phase markers and arms a C faulthandler watchdog. Its outer unconfigure wrapper rearms after other plugins may cancel the watchdog, preserving atexit/finalization visibility. The runner polls child status before timeout killing to distinguish a live child from inherited-pipe EOF delay; forkserver handles gain poll support.

## Code

Source paths touched by this feature group (including regression fixtures):

- `scripts/pytest_exit_diagnostics.py`
- `scripts/run_tests_launch.py`
- `scripts/run_tests_parallel.py`
- `tests/scripts/test_pytest_exit_diagnostics.py`

Mail entries in the ordered series:

- 4056: [`f0c734f5b7a6ffec0b9979d390be76edd903a56e`](patches/4056-daveey-capture-late-pytest-exit-stacks-for-derived-session-investigation.patch)

## Tests

`tests/scripts/test_pytest_exit_diagnostics.py`: real synthetic atexit and finalizer hangs yield stack output after passes and unconfigure while the child stays alive; ordinary pass/fail exits remain unchanged. Real forkserver success/poll, scoped injection without argument mutation, non-consuming poll and pre-kill status reporting are asserted, retaining timeout exit 124.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

The historical hang was not reproduced or explained by this change. Diagnostics intentionally survive teardown; deadlines, cleanup, retries and exit statuses are unchanged.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

The helper is a pytest plugin scoped by the core test launcher, not a Hermes agent plugin.
