# fix(tests): [daveey] metrics harness passes on macOS ls and with the live venv's bin on PATH

Patch 0708, source `00c401c74a7f2a4ac280904aa4c97a50fa6371d8`.

## Behavior

fix(tests): [daveey] metrics harness passes on macOS ls and with the live venv's bin on PATH. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_path_metadata_exemption_tracks_path_changes(protected_home, monkeypatch`
- `def test_foreground_terminal_commands_count_by_kind_and_outcome(direct_runtime,`
- `class HomeIOGuard:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> test_foreground_terminal_commands_count_by_kind_and_outcome hard-coded GNU ls's
> exit 2 for a missing path; BSD/macOS ls exits 1. Assert nonzero instead.
>
> home_io_guard exempted stat of <PATH entry>/<name> (command lookup) but not of the
> PATH entry itself, so _resolve_hermes_bin_dir's isdir(dirname(which('hermes')))
> tripped the real-home guard whenever a dev shell had ~/.hermes/hermes-agent/venv/bin
> on PATH (terminal + file-tool tests failed). Metadata-only stat of a PATH entry is
> now exempt; listing/writing inside it is still refused (new tripwire test).

## Code

- `tests/hermes_cli/test_real_home_tripwire.py`
- `tests/hermes_cli/test_shared_metrics_harness.py`
- `tests/home_io_guard.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_real_home_tripwire.py`
- `tests/hermes_cli/test_shared_metrics_harness.py`
- `tests/home_io_guard.py`

New or changed test definitions present in the source diff:

- `test_path_entry_itself_is_lookup_metadata_but_not_readable`
- `test_relative_path_metadata_exemption_tracks_working_directory`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
