# Cron finalization survives a slow state.db write

Patch: 0875 (fork `15ef9412b5`).

## Behavior

When a state.db write during cron session finalization outlives `cron.cleanup_timeout_seconds` (another process holds the WAL write lock):

- `_finalize_cron_session` returns. It no longer raises `session finalization disabled after prior cleanup failure`, and `run_job` returns normally.
- The agent is still torn down, or handed off via `defer_agent_teardown`, because the `run_job` finally block guards the finalize call.
- One WARNING is logged: `Job '<job>': session <id> finalization timed out (state.db busy; titled=.. ended=..); retrying on a fresh connection`.
- A daemon thread (`cron-finalize-retry-<job>`) waits `cron.finalize_retry_delay_seconds` (default 30) and retries the missing title and `end_session` on a fresh `SessionDB` for the same file, under the same cleanup bound. The agent's own `end_session` on close is disarmed so the retry owns the row.
- Title fallbacks (`get_next_title_in_lineage`, then `cron <job_id>`, then an id-stamped title) are built inside their own guard. A disabled proxy cannot escape them.
- If the retry also fails, one WARNING names the job and session (`finalize retry failed` / `finalize retry did not complete` / `finalize retry left it titled=.. ended=..`). Nothing raises back into the job.

A finalization that does not time out is unchanged: title, then `end_session`, then disarm `agent._end_session_on_close`.

## How it works

`_BoundedCronSessionDB` still fails fast after the first timed-out write. What changed is that nothing in the finalize path lets that error out, and a timed-out pass queues one retry instead of leaving the row untitled and unended.

`_write_cron_title` tries the caller titles, and on the first failure inserts the lineage title and an id-stamped title into the candidate list, each inside try. `_finalize_cron_session` wraps `_finalize_cron_session_steps`. When the bounded proxy is disabled and title or end did not stick, it logs the one WARNING and calls `_queue_cron_finalize_retry`. The retry opens `SessionDB(db_path=...)` (not the disabled proxy) after the delay, inside `_run_cron_cleanup_with_timeout`.

## Code

`cron/scheduler.py`:

- `_finalize_cron_session`, `_finalize_cron_session_steps`
- `_write_cron_title`, `_next_lineage_title`, `_classify_cron_end_reason`
- `_queue_cron_finalize_retry`, `_open_fresh_cron_session_db`, `_cron_finalize_retry_delay_seconds`
- `run_job` finally: the `_finalize_cron_session` call is inside try, so teardown / hand-off always runs

## Tests

`tests/cron/test_cron_finalize_slow_write.py` (4 tests):

- `test_finalize_survives_title_write_timeout_and_retries_on_fresh_connection`: a title write that sleeps past the cleanup bound; finalize returns, and the retry writes a title and `end_session`.
- `test_run_job_survives_title_write_timeout_and_tears_agent_down`: `run_job` with that DB does not raise, and the agent is torn down.
- `test_run_job_hands_agent_off_when_finalize_raises`: if finalize itself raises, `run_job` still hands the agent off.
- `test_finalize_disabled_proxy_logs_one_warning_naming_job_and_session`: the WARNING is one line and names the job and the session.

Ran for this mirror on fork `origin/main` `a65917ae05` with the hub test-environment interpreter (`pytest -q -p no:cacheprovider`, scratch `HERMES_HOME`): 4 passed in the same run as the gap-commit tests (31 passed in 13.67s: these 4, `test_oom_reclaim_does_not_take_a_second_write_txn`, `test_reimported_hermes_modules_restores_process_objects`, and `tests/hermes_cli/test_kanban_wait_replica.py`). Untested here: a live cron job whose state.db lock is held by another process.

## Applying

Append after 0874. Do not renumber 0877 or 0879: those were published ahead of this range. See the numbering note in [README.md](README.md).

## Gotchas

- The retry is one shot, on a daemon thread. If the process exits before the delay, the row can stay unended.
- A fake `SessionDB` with no `db_path` skips the retry (`finalize retry skipped: no fresh state.db connection`) and logs a WARNING. The job still does not crash.
- The write-lock holder is not fixed here. A lock held for tens of seconds still stalls every other writer; finalization just stops turning that into a job crash.

## Plugin?

No. Finalization is core cron.
