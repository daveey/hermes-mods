# Compound shell input flow and printed function producers

Mirror entries: 4761, 4762, 4777.

## Behavior

Output process substitutions and function-produced text feeding startup shells receive the appropriate shell-sink analysis. A first remote host running an exact git apply command continues to receive patch data even through shell payload wrappers.

## How it works

The compound pipeline walker optionally treats output substitutions as implicit stdin pipes, carrying that input through lists and nested groups and restoring the enclosing input on close. Printed-function discovery resolves visible definitions, transitive calls and redefinitions only upstream of the startup-shell consumer. Each definition is walked once per command. A separate first-host flag survives shell payload recursion without resetting its recursion budget; the narrow git apply exception does not propagate across another remote-host hop.

## Code

- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_printed_functions.py`
- `tools/shell_heredoc.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Compound-output-sink tests include generated harmless Bash markers, unfed controls and opt-in traversal. Printed-function tests cover indirect calls, next-line and compound bodies, unrelated definitions and upstream boundaries. Session-alias tests cover wrapped patch consumers, further-host negatives and recursion limits.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/cron/test_lifecycle_guard_compound_output_sink.py`
- `tests/cron/test_lifecycle_guard_printed_functions.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Unknown startup-defined or dynamically built functions are not resolved by visible-definition discovery. The patch-data exception is exact argv handling, not permission for arbitrary commands, options, Python launchers or sourcing stdin.
