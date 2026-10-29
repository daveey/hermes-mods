# fix(cron): backfill also ends orphaned cron session rows whose attempt was pruned or settled unfinalized [daveey]

Patch 0891, source `ac1eb73e0e49fa9c419109293ad552363645d6bb`.

## Behavior

fix(cron): backfill also ends orphaned cron session rows whose attempt was pruned or settled unfinalized [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def backfill_interrupted_cron_sessions(`
- `def test_cleanup_thread_keeps_profile_scoped_hermes_home(monkeypatch, tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Follow-up to fec6ced730. Of hermes-hub's 6 stuck cron_* rows only 3 had an 'unknown'
> ledger attempt; 1 was a 'failed' attempt whose finalization was disabled and 2+ had their
> attempt pruned, so the ledger-driven backfill could not reach them.
> end_orphaned_cron_sessions() ends unended cron_* rows older than 1 h whose job has no
> claimed/running attempt claimed at or before the row started; backfill_interrupted_cron_sessions()
> runs it after the ledger pass. Tests: 2 new (old orphan ended, young/live-owned spared).

## Code

- `cron/interrupted_sessions.py`

## Tests

Source test files changed by this commit:

- `tests/cron/test_interrupted_cron_session_end.py`

New or changed test definitions present in the source diff:

- `test_orphan_sweep_ends_old_rows_without_a_live_attempt`
- `test_orphan_sweep_spares_a_row_a_live_attempt_may_own`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
