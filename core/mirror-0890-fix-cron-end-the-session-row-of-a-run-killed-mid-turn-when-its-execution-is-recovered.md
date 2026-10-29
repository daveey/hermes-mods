# fix(cron): end the session row of a run killed mid-turn when its execution is recovered [daveey]

Patch 0890, source `fec6ced730a04f85fe0949df184b530850c30420`.

## Behavior

fix(cron): end the session row of a run killed mid-turn when its execution is recovered [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def recover_interrupted_executions() -> int:`
- `def terminalize_dead_owner(execution_id: str, *, reason: str) -> bool:`
- `+"""End the state.db session rows of cron executions that were interrupted mid-turn.`
- `+"""A cron run killed mid-turn must not leave its state.db session row unended forever.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> recover_interrupted_executions() and terminalize_dead_owner() marked a dead owner's
> attempt 'unknown' but never touched its state.db session: _finalize_cron_session only
> runs in the owner, so a run killed by a gateway restart left its cron_<job>_* row
> untitled with ended_at NULL forever (6 such rows on hermes-hub in 7 days).
>
> Recovery now titles (if untitled) '<job name> · <time> (interrupted)' and ends those
> rows with end_reason 'cron_interrupted' (plus the compression tip). The ledger has no
> session id, so the row is matched as the unended cron_<job_id>_* session started between
> the attempt's claim and the next claim of the same job, which keeps a newer live run out.
> Bounded (15 s daemon thread), never raises. backfill_interrupted_cron_sessions() repairs
> already-recovered attempts.
>
> Tests: tests/cron/test_interrupted_cron_session_end.py (4 red before, 6 green after);
> tests/cron 1532 passed.

## Code

- `cron/executions.py`
- `cron/interrupted_sessions.py`

## Tests

Source test files changed by this commit:

- `tests/cron/test_interrupted_cron_session_end.py`

New or changed test definitions present in the source diff:

- `test_recovery_ends_and_titles_interrupted_session`
- `test_recovery_keeps_existing_title_and_uses_job_name`
- `test_recovery_does_not_end_a_newer_live_run_of_the_same_job`
- `test_terminalize_dead_owner_ends_session`
- `test_session_cleanup_failure_never_breaks_recovery`
- `test_backfill_ends_sessions_of_already_recovered_executions`
- `test_cleanup_thread_keeps_profile_scoped_hermes_home`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
