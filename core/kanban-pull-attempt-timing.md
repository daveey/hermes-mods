# Kanban pull errors record the failed HTTP attempt's budget and elapsed time

Patch **2001**: source `82c453a9c739d3da9cd74126e106227c1948e747`, "fix(kanban): pull board_error records the failed
HTTP attempt's budget/elapsed [daveey]". Related: reserve/repin timing in
[kanban-queue-reasons-repin.md](kanban-queue-reasons-repin.md).

## Behavior

When a dispatch-tick feed pull fails, the error is written to every worker-first `ready` card the origin
owns (`stage=pull`). Those records now carry `budget_ms` and `elapsed_ms` for the HTTP attempt that failed.
Previously only reserve and repin were timed. A timeout on a starved page-2 GET therefore used to look the
same as a slow page-1 GET.

- `budget_ms` is what the shared request deadline had left when *that* attempt started. `elapsed_ms` is how
  long that attempt took to fail. Neither covers the whole pull.
- **Retries.** A stale kept-alive socket retry restarts the clock: only the retry is timed. A lost POST answer
  is never replayed, and its single attempt is the one stamped.
- **Edge cases.** If the budget was already exhausted before an attempt, no attempt was made and nothing is
  stamped. With no deadline, only `elapsed_ms` is recorded. Both transports stamp: the kept-alive
  `http.client` path and the `urlopen` path.
- **Bad values.** Absent, malformed, non-finite or raising stamps are omitted, and the error is still
  recorded unchanged. Values use the same whole-millisecond rounding and clamping as reserve/repin.
- **Nothing else changes.** This is diagnostics only. Exception classes, URLError wrapping, attempts,
  deadlines, POST no-replay, event and comment fan-out and dedupe, and reserve/repin timing are unchanged.
  Timing never creates a new pull event.

## How it works

- `hermes_cli/kanban_sync_client_attempt.py` (new) provides `note_attempt(exc, deadline, started, ended)`.
  It sets `exc.board_attempt = (budget_s, elapsed_s)` only if no stamp is present yet, so the innermost
  attempt wins. A redirect's own `urlopen` attempt isn't overwritten. Stamping never raises.
- `hermes_cli/kanban_sync_client.Hub`:
  - The kept-alive loop computes `attempt_timeout` and `started` once per attempt. It wraps the whole
    attempt in `except Exception: note_attempt(...); raise`.
  - `_call_urlopen` does the same around its `open_request`.
- `hermes_cli/kanban_queue_reason.attempt_timing(error)` reads the stamp. It accepts only a 2-tuple, maps
  it to `TIMING_KEYS` through `_ms`, and returns `{}` on any problem. `record_pull_error` passes the result
  as `timing=` to each `record_error(..., stage="pull")`.
- The client never imports the queue layer. The sibling module holds the attribute name for both layers.

## Code

`hermes_cli/kanban_sync_client_attempt.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_queue_reason.py`.

## Tests

I didn't run these tests for this document. They're listed from the source diff.

- `tests/hermes_cli/test_kanban_pull_attempt_timing.py`:
  - `test_first_page_timeout_records_the_full_attempt_budget`
  - `test_second_page_timeout_records_only_the_budget_left_after_page_one_apply`
- `tests/hermes_cli/test_kanban_pull_attempt_timing_edges.py`. Most cases run against a local server
  fixture with a boxed monotonic clock:
  - `test_stale_get_retry_failure_is_timed_from_the_retry_only[drop|status:503]`
  - `test_lost_post_answer_is_never_replayed_and_stamps_its_one_attempt`
  - `test_budget_exhausted_before_the_retry_records_no_timing`
  - `test_no_deadline_stamps_elapsed_only`
  - `test_urlopen_path_stamps_its_attempt`
  - `test_absent_stamp_keeps_existing_pull_records_unchanged`
  - `test_malformed_stamps_are_omitted_and_never_mask_the_error[...]`
  - `test_a_raising_stamp_attribute_still_records_the_error`
  - `test_stamping_never_masks_and_the_innermost_attempt_wins`
  - `test_timing_never_makes_a_new_pull_event`

The commit message reports these implementer results, which I didn't reproduce: RED (2 failed) → GREEN on the
first file, 23 edge cases, a focused suite of 200 passed, and mutants on each added condition killed.

**Untested:** a real Board service and a real dispatcher tick. The tests drive `Hub` against a local
stand-in and call `record_pull_error` directly. The stamp also appears on errors from non-pull Board calls,
but only the pull recorder reads it.

## Applying

Apply with the series (README § Applying). The `Hub` hunk re-indents the whole kept-alive attempt body into
a new `try`. If it conflicts, re-create the wrapper by hand, keeping each existing `except` branch exactly as
it is.

## Gotchas

- **Read the fields per attempt.** `budget_ms`/`elapsed_ms` describe a single HTTP attempt, not the pull.
  Don't sum them across a record's fan-out.
- **Stamped exceptions.** The stamp is an attribute on the exception object, so a caller that re-wraps the
  exception in a new one loses the timing. Inside `Hub`, `note_attempt` runs on the exception as it leaves the
  attempt, which is the already-wrapped `URLError`, `HubError` or `Resync`.

## Plugin boundary

There's no plugin equivalent. The timing has to be captured inside the Board HTTP client's attempt loop.
