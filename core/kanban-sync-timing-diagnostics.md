# Kanban sync: bounded opt-in timing diagnostics

Patches **2003–2004**:

| Ordinal | Source SHA | Change |
| --- | --- | --- |
| 2003 | `27720df1918ad0d7e48f7fa1ab360bc2cf6be93d` | "feat(kanban-sync): bounded opt-in sync timing diagnostics": writer, hooks, analyzer |
| 2004 | `0cbbc98ed684c4c5c1a561bcd42f335a53e152d5` | "fix(kanban-sync): timing analyzer fails closed on prefix, schema and provenance": review corrections |

**Status.** This is instrumentation only, and it is **off by default**. These patches don't establish
that sync latency is acceptable: that question is **unproven**. No capture has been compared against a
matched legacy (pre-instrumentation) baseline, and the analyzer subtracts no baseline. The source
commits make no live acceptance claim, and nothing here says the change has been accepted for rollout.

## Behavior

- **Arming.** Diagnostics are off unless the installation-root `config.yaml` key
  `kanban.sync.diagnostics.timing_until` holds a finite Unix time **at most 600 s ahead** when the sync loop
  first sees it. These values are all off: missing, null, non-numeric (including booleans and strings),
  non-finite, already past, more than 600 s ahead, or an unreadable or malformed config. An overlong value
  never arms later, even once it comes within 600 s. A value arms at most once per process. Setting a new
  value starts a new capture.
- **Off cost.** When off, the loop pays one cached config read per iteration. It creates no file and no
  thread, and every product callable runs exactly once, as before.
- **When a capture ends.** The loop checks the key at its safe boundary, just before it waits for work. A
  capture ends at the first boundary after the earliest of these: the configured time, 600 s of monotonic
  time (even if wall time stalls), a changed or removed value, or a config read failure. There's no hard
  timer, so the stop can overrun by up to one sync round plus one wait. A capture also ends at once on a
  write failure, on the 32 MiB file cap (the stop marker still fits inside the cap), or when the loop exits.
  Each capture ends with a `stop` record and its reason: `expired`, `bound`, `disabled`, `config_error`,
  `write_error`, `file_cap` or `loop_exit`.
- **What is recorded.** Records are JSONL in `<root>/logs/kanban-sync-timing.jsonl`. Times are monotonic
  ns, with wall clock used only for provenance and expiry. Every record is allowlisted to numbers,
  booleans and enums, and carries a sequence number. Boards are process-local ordinals. No board names,
  IDs, URLs, keys, messages or paths are written.
  - `start` holds the schema, pid, a random run nonce, the bounds, and the code identity the process
    *booted* on: `code`, `code_src=boot|none`, and `disk_moved` when the checkout has moved since.
  - `chg` is one decoded Board feed change frame.
  - `sel` is the round's selection. It's taken under the same lock as the change hook, so each change is
    either claimed by this round or left pending, never both.
  - `loc` is the first observation of a board's pending outbox.
  - `svc` is each board's actual service, with the claimed watermark, the outcome (`ok|error|raised`) and
    the error class. A failed or raised service never counts as a match: its claim goes back to pending,
    with a failure count.
  - `rep` is the metrics Reporter's outer span, with `collect`, `limit`, `cpu`, `adv` and `send` component
    spans. `send` covers the whole private bounded transport.
  - `wait`, plus `flush` (the cost of a flush is recorded by the next one).
  - `pend` records unserviced boards (up to 32), followed by `stop`.
- **Invalid captures.** A capture is marked `valid: false` if a record is lost (queue full), the
  diagnostic itself raises, a write fails, or the file cap is hit. It's never reported as green in those
  cases.
- **Rotation.** Starting a new capture rotates a file of at least half the cap to `.1`, replacing any older
  `.1`. Archive a capture before arming the next one.
- **Known cost while armed.** `select` holds the diagnostic lock across `SyncWork.select`, which includes
  its per-board outbox reads. A feed callback can therefore wait that long before returning to its socket.
  The wait shows up in `chg.lk`.
- **Analyzer.** `python -m hermes_cli.kanban_sync_timing_report <file>` prints a JSON array with one
  summary per capture and exits 1 unless the file holds at least one capture and every capture is valid. It applies no thresholds and no baseline subtraction.
  - It reports these measurements:
    - validity;
    - the latency from each decoded change to its own board's next successful service, split into
      cohorts that did and didn't overlap a report span, with the overlap residual;
    - coverage (`none` when no change overlapped a report);
    - failed and raised services;
    - `unserviced`: censored changes still pending at stop;
    - `unserviced_boards_truncated`: boards beyond the 32 `pend` records (2004);
    - report durations and their components;
    - send outcomes;
    - diagnostic overhead.
  - Since 2004 the analyzer fails closed. Records before the first `start` are kept as their own leading
    capture, which fails as `no_start_marker`. A file with no complete capture never passes and never
    crashes. Validation also flags `schema_mismatch`, `unknown_event`, `field_not_allowed` (the writer's own
    allowlist), `identity_unverified` (no boot code, `code_src` other than `boot`, or `disk_moved`) and
    `malformed_record`, which is reported instead of a crash.
- **Writer fixes (2004).** The writer resolves code identity before it opens the fd, so an identity
  failure leaks no fd and still captures, marked unverified. If the start fails after the fd is open, the
  fd is closed.

## How it works

- `hermes_cli/kanban_sync_timing.py` (new) holds `TimingDiag`: one process instance (`_DIAG`), built with
  injectable root, config reader, clocks, code identity, queue/file limits and writer. Its facade functions
  are `decoded_change`, `select`, `board_start`/`board_end`, `report`, `span`, `note_cpu_sample`, `wait` and
  `stop`. Each one runs the product callable exactly once, outside the diagnostic `try`. `boundary()`, run
  from `wait`, reads the config and starts, stops or flushes the capture. Records queue in memory (at most
  50 000) and are flushed only at the boundary.
- `hermes_cli/kanban_sync_client.py` adds the hooks:
  - `FeedWatcher` routes `on_change` through `decoded_change`.
  - `_run_forever` wraps `work.select`, `metrics.report_due` and `wait_for_work`.
  - `sync_all_once` brackets each board's `sync_board` with `board_start`/`board_end`.
  - `run_forever`'s `finally` calls `sync_timing.stop()`.
- `hermes_cli/machine_metrics.Reporter` wraps collect/limit/cpu/adv/send in `timing.span(...)`. Outside a
  `report` scope, these spans don't record anything.
- `hermes_cli/kanban_sync_timing_report.py` (new) provides `load`, `captures`, `validate`, `summarize` and `main`.
- `hermes_cli/config_runtime_keys.CORE_RUNTIME_KEYS` registers `kanban.sync.diagnostics.timing_until`, so
  it counts as a known key.

## Code

`hermes_cli/kanban_sync_timing.py`, `hermes_cli/kanban_sync_timing_report.py`, `hermes_cli/kanban_sync_client.py`,
`hermes_cli/machine_metrics.py`, `hermes_cli/config_runtime_keys.py`.

## Tests

I didn't run these tests for this document. They're listed from the frozen-tip source.

- `tests/hermes_cli/test_kanban_sync_timing.py`. These are unit tests against an injected `TimingDiag`
  harness, plus the analyzer.
  - From 2003:
    - Config and arming:
      - `test_off_or_invalid_config_writes_nothing_and_passes_every_call_through[...]`
      - `test_unreadable_config_is_off[...]`
      - `test_malformed_config_tree_is_off`
      - `test_exactly_600_s_arms_and_the_monotonic_bound_stops_it_even_if_wall_time_stalls`
      - `test_overlong_value_never_arms_later_when_it_comes_within_600_s`
      - `test_capture_stops_with_an_explicit_marker[...]`
      - `test_a_changed_value_ends_the_capture_and_arms_a_new_one`
      - `test_off_path_does_no_diagnostic_work`
      - `test_timing_key_is_a_known_config_key`
    - Change, selection and service matching:
      - `test_decoded_changes_match_their_own_boards_actual_service`
      - `test_an_interleaved_other_board_change_is_not_served_by_this_boards_claim`
      - `test_a_raised_service_is_no_match_and_keeps_the_change_pending_to_the_stop_marker`
      - `test_local_pending_records_first_observation_not_each_poll`
      - `test_service_from_another_thread_is_recorded_but_never_consumes_the_loops_claim`
      - `test_threaded_feed_callbacks_race_the_flush_without_loss`
      - `test_change_and_selection_are_atomic`
      - `test_hooks_off_the_loop_thread_record_no_selection_or_report`
    - Failure handling and record hygiene:
      - `test_queue_overflow_is_counted_and_invalidates`
      - `test_file_cap_stops_with_marker_inside_the_cap`
      - `test_write_failure_stops_capture_without_breaking_sync`
      - `test_a_failing_diagnostic_never_skips_or_repeats_the_product_call`
      - `test_records_carry_no_names_urls_or_messages`
      - `test_a_record_outside_the_allowlist_is_dropped_and_invalidates`
    - Reporter spans:
      - `test_report_record_spans_collect_sample_and_the_whole_send[...]`
      - `test_reporter_outside_a_report_scope_is_unchanged`
      - `test_flush_cost_is_recorded_as_its_own_span_outside_measured_records`
    - Analyzer: `test_analyzer_rejects_restart_and_mixed_identity`
  - From 2004:
    - `test_records_before_the_first_start_are_retained_and_fail_the_file[...]`
    - `test_a_file_with_no_complete_capture_never_passes_and_never_crashes[...]`
    - `test_validate_rejects_schema_event_allowlist_and_unverified_identity[...]`
    - `test_a_capture_without_boot_identity_is_unverified`
    - `test_malformed_but_allowlisted_records_are_invalid_not_a_crash`
    - `test_unserviced_changes_and_truncated_boards_are_separate_counts`
    - `test_an_identity_failure_leaks_no_fd_and_still_captures_unverified[RuntimeError|KeyError]`
    - `test_a_start_failure_after_open_closes_the_fd`
- `tests/hermes_cli/test_kanban_sync_timing_loop.py`. These run the real `run_forever` loop, the real
  `Reporter` and a real WebSocket feed against a local Board stand-in. They need `websockets`.
  - `test_real_loop_records_full_report_cost_and_matches_a_change_to_its_boards_service`. A change frame
    arrives during a delayed metrics POST, and the test asserts the full report cost, the change inside the
    send span, the match to that board's later service, and coverage `overlap`. Since 2004 this test
    injects a boot identity, because a test process has none.
  - `test_off_loop_creates_no_diagnostic_file_or_thread`.

  The test module's docstring says it "validates the instrumentation only; it is not a live acceptance
  measurement and it subtracts no baseline."

The commit messages report these implementer results, which I didn't reproduce:

- 2003: an 86-file area suite with 1039 passed, and 44 exact-anchor mutants killed.
- 2004: 16 new tests red, then 187 passed and 0 failed across timing, timing_loop, machine_metrics,
  config_runtime_keys and kanban_sync_client.

**Untested:**

- A capture on a live hub or gateway against the real Board service.
- A matched before/after latency comparison.
- The `python -m` CLI entry as a subprocess. Tests call `report.main([...])` in-process.
- The real `hermes_bootstrap` boot fingerprint path. Tests stub `_code_identity`.
- Rotation to `.1` with an existing large file.

## Applying

Apply 2003 then 2004 with the series (README § Applying). 2003's `kanban_sync_client.py` hunks sit next
to 2001's `note_attempt` import, so apply 2001 first.

## Gotchas

- **Clock-relative arming.** The 600 s ceiling is measured against the clock when the loop first reads the
  value. If you write a time 10 minutes ahead and the loop reads it a few seconds later, it's still within
  the limit. If you write 11 minutes ahead, it never arms. Write a fresh value for each capture.
- **Installation root only.** Only the installation root's `config.yaml` is read. A profile's config
  doesn't arm diagnostics.
- **Unverified identity without bootstrap.** A process started without `hermes_bootstrap`, or a checkout
  moved after boot, produces captures that the analyzer rejects as `identity_unverified`. That's
  intended.
- **Analyzer exit code.** A non-zero exit from the analyzer is expected for any file that holds an orphan
  prefix or an incomplete capture, even if a later capture in it is valid.

## Plugin boundary

There's no plugin equivalent. The hooks sit inside the core sync loop, the feed watcher thread and the metrics
Reporter, all of which run in the gateway's sync process, where no plugin hook is available.
