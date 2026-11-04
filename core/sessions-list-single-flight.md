# GET /api/sessions: single-flight and data-stamp cache

Patches: **1884** (`92e3135edd`, `perf(sessions): single-flight + data-stamp cache for GET /api/sessions [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- Identical concurrent `GET /api/sessions` calls (same params and state.db) share one in-flight computation, and a finished result is reused for 2 s while the data stamp (SQLite WAL-index header plus db/WAL stat) is unchanged; any committed write misses at once, and no WAL means no caching. The source author measured 15 concurrent calls on a 7.2 GB state.db: p95 2.14 s to 0.15 s (not rerun here).

## How it works

See Code; each caller gets its own copy of the payload rows.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1884: hermes-hub: Desktop reconnect bursts of ~15 identical /api/sessions calls each ran the recent-order list SQL (~0.1-0.3 s on a 7 GB state.db) plus the activity annotations in its own threadpool slot; the burst queued behind itself (2-7 s per call, ~3,000 slow calls in 30 h, 131 requests parked in run_in_threadpool).

## Code

`hermes_cli/session_list_flight.py`, `hermes_cli/web_routers/sessions.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_session_list_flight.py`: `test_data_stamp_is_stable_across_reads_and_changes_on_every_write`, `test_no_wal_means_no_stamp_and_no_caching`, `test_concurrent_identical_calls_run_the_sql_once_and_a_write_invalidates`, `test_ttl_expiry_and_different_params_recompute`, `test_leader_error_reaches_followers_and_is_not_cached`, `test_follower_computes_on_its_own_when_the_leader_hangs`, `test_expired_entries_are_dropped_on_the_next_insert`, `test_get_sessions_route_coalesces_and_sees_writes`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
