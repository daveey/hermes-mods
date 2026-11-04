# Mirror 1924–1932: features

Patches **1924** through **1932**. The series ends at the frozen fork tip `fb3e4519f4bfec0bf480b026dd8dbc152b564b65`.
Each section below covers one feature. 1930 and 1931 share a section because 1931 only fixes a test that 1930 added.

| Patch | Source SHA | Feature |
|---|---|---|
| 1924 | `26ffa045d655` | TUI prompt-wait RPC errors carry `reset_at`, not the replay clock (tests only) |
| 1925 | `fbd4ed1385a4` | Kanban metadata files: a whole-value path names only itself |
| 1926 | `c6fb0025a60c` | Cron waits out a claim's `admission.lock` hold; a still-busy lock defers the tick |
| 1927 | `2afdcca36fbb` | Gateway: bounded ACP shutdown bookkeeping, run off the owner loop |
| 1928 | `703280de25f7` | Kanban sync client: a revision-fenced reserve is retried after a reused socket drops |
| 1929 | `fbacb56f0d5a` | Agent: a Desktop terminal batch timeout stops the batch, not the turn |
| 1930–1931 | `ee7bd5477d78`, `0833958728f5` | Redaction: random-token secret fields and subprocess argv in tool output |
| 1932 | `fb3e4519f4bf` | Kanban: accept the `exact_head` review metadata alias |

**Test status for every section:** the tests named below are the ones these patches add or change. The canonical runner on the frozen source tip passed 468 tests across all 12 changed test files on Linux; macOS-only cases were skipped. See [verification](mirror-1932-verification.md). Where a commit message records
the author's own RED/GREEN or mutant runs, this document says that the commit reports them; this pass did not
reproduce them.

**Applying (all sections):** use the full-series replay recipe in `core/README.md` § Applying, which starts at the
documented replay base and applies every bridge and ancestry-only marker in order. These patches are not certified to
apply standalone or in isolation. Several of them change functions or test helpers introduced by earlier patches,
for example `_strict_build`, the `_spawn_lock_holder` and `_root` test helpers, `_call_kept_alive`, `_TerminalBatch`
and the dependency-review helpers. No section adds a plugin, and none needs a plugin to work: every change lives in
core.

---

## 1924: TUI prompt-wait RPC data keeps `reset_at` (tests)

### Behavior

This patch changes no production code; it adds tests that pin two existing behaviors. (1) When a submitted
prompt waits on an agent build that failed on a subscription-quota wall, the RPC error `_wait_agent_for_prompt`
returns and the `message.complete` error payload carry the pool's `reset_at`. Neither exposes the internal
`failure_resets_at` replay field. The stored `session["agent_failure"]` record keeps `failure_resets_at`, so
automation replay can still read it. This holds on both failure exits: a dead build thread that never set ready, and
a finished build that set ready with its error. (2) `client_failure_fields` uses `failure_resets_at` only to fill an
unset `reset_at`. A `reset_at` already in the record is never overwritten, and the record itself is not changed.

### How it works

`_prompt_turn_on_quota_build` builds a session on a `SubscriptionQuotaUnavailable` build through the existing
`_strict_build` helper. It then runs `server._run_after_agent_ready` (the prompt.submit turn thread) with a spy on
`_wait_agent_for_prompt` and captures `write_json` frames. The parametrized ids are `dead-build` (the build thread
is dead and `agent_ready` is unset) and `final-branch` (`agent_ready` is set).

### Code

- `tests/tui_gateway/test_credential_failure.py` (63 lines added; no source files change).

The commit message is a long squashed upstream log covering approval, CI, Discord, Desktop notarization and other
work. Of that log, only this test file is in the diff. Those other topics are not part of this patch.

### Tests

- `tests/tui_gateway/test_credential_failure.py::test_prompt_wait_rpc_error_reads_reset_at_not_the_replay_clock[dead-build|final-branch]`
- `tests/tui_gateway/test_credential_failure.py::test_client_failure_fields_keeps_a_reset_at_already_set`

The changed test file(s) passed in the targeted Linux source run. The tests drive server functions in-process. A live Desktop or TUI client
receiving the frame is untested.

### Applying

Use the full-series recipe (see the top of this document). The tests rely on `_strict_build`,
`client_failure_fields` and the `failure_resets_at` split documented in `core/tui-error-payload-reset-at.md`.

### Gotchas

- The squashed commit message can mislead: read the diffstat, not the bullet list, to see what this patch contains.
- `failure_resets_at` is internal. Client-visible payloads must keep exposing only `reset_at`.

### Plugin

None.

---

## 1925: Kanban metadata files, a whole-value path names only itself

### Behavior

This is a follow-up to the completion-metadata file copier (`core/kanban-handoff-metadata-files.md`). Suppose a
completion metadata string is, as a whole, an existing file under the workspace, for example
`<ws>/replay report v2.md`. That file is now copied once, and no phantom skip entry is recorded. Before this patch,
the string was also split into tokens, so `<ws>/replay` appeared under `attached_from_workspace_skipped`. A string
that is not one whole existing file is still split into tokens, so `"<ws>/a.md <ws>/b.md"` still copies both files.

### How it works

In `_candidates`, the whole-value branch (`whole.startswith(prefix + "/")` and `Path(whole).is_file()`) now ends
with `continue`, so the regex token pass is skipped for that string.

### Code

- `hermes_cli/kanban_metadata_files.py` (`_candidates`: docstring and one `continue`).
- `tests/hermes_cli/test_kanban_metadata_files.py` (new tests; adds `import os`).

### Tests

New in `tests/hermes_cli/test_kanban_metadata_files.py`:

- `test_workspace_paths_that_resolve_outside_are_not_copied`: a symlink, and a `ws/../` path, that resolve outside
  the workspace are recorded as skipped with an "outside" reason and are not copied.
- `test_a_whole_value_path_with_spaces_is_copied`: the file is copied once into the task attachments directory.
- `test_a_whole_value_path_with_spaces_records_no_phantom_skip`
- `test_a_value_naming_two_paths_copies_both`
- `test_dir_and_worktree_workspace_files_are_not_copied[dir|worktree]`: the workspaces sit under the managed root,
  so only the workspace-kind gate keeps their files out.
- `test_scratch_workspace_outside_managed_storage_is_not_copied`

The commit message reports RED results for the pre-fix code and for four named mutants, and 12 passing tests on the
fixed tree. This pass did not reproduce those runs; the changed test file passed in the targeted Linux source run. The tests enter through
`kb.complete_task`. CLI and tool completion paths are untested here.

### Applying

Use the full-series recipe.

### Gotchas

- The whole-value check needs the file to exist when the card completes. A spaced path to a missing file falls
  through to token splitting, so its leading fragment can still appear as a skip.

### Plugin

None.

---

## 1926: Cron waits out a claim's `admission.lock` hold

### Behavior

When the hub CPU gate is on, a Kanban claim holds the core-update `admission.lock` for about 1 to 2.25 s
(`CLAIM_MAX_HOLD_S`). Before this patch, cron's tick made one 50 ms attempt to reserve. `AdmissionBusy` is a
`TimeoutError`, so the tick failed open: it dispatched with no reservation, the run was invisible to the update
drain, and a restart could go ahead under it. Now:

- The tick's `update_admission()` and the per-run `_take_run_reservation()` wait up to
  `backend_retirement.BUSY_WAIT_S` (`CLAIM_MAX_HOLD_S + 0.5`) for the lock.
- If the lock is still busy after that wait, the tick yields False: it dispatches nothing and advances no
  `next_run_at`, and due jobs fire once on a later tick. An `INFO` line logs the deferral.
- Any other `OSError`, such as an unwritable fence root, still fails open and dispatches, as before.
- Cron registers no turn-waiter marker while it waits, so claims do not defer to cron.

### How it works

`admission_fence.reserve()` and `extend()` gain a keyword-only `timeout` argument. Its default stays 0.05 s, for
callers that hold the backend session-map lock. Cron holds no session lock, so it passes `BUSY_WAIT_S`.
`update_admission()` catches `AdmissionBusy` before the generic `(OSError, TimeoutError)` branch, which is needed
because `AdmissionBusy` is a subclass of `OSError`.

### Code

- `hermes_cli/admission_fence.py`: `reserve(home, *, timeout=0.05)`, `extend(home, *, timeout=0.05)`.
- `cron/scheduler_tick.py`: `update_admission()`.
- `cron/scheduler.py`: `_take_run_reservation()`.
- Tests: `tests/cron/test_update_admission_hold.py`, `tests/hermes_cli/test_admission_fence.py`.

### Tests

These tests use a real lock holder: a `multiprocessing` spawn child holds the lock with `flock`.

- `tests/cron/test_update_admission_hold.py::test_tick_waits_out_a_claim_holding_the_lock_and_dispatches_under_a_reservation`
  (a 1.5 s holder: the tick waits at least 0.5 s, dispatches with at least one reservation, and never shows a
  turn-waiter marker).
- `...::test_tick_defers_when_the_lock_stays_busy_past_the_wait_budget` (a holder longer than `BUSY_WAIT_S`: the
  tick returns 0, dispatches nothing and does not advance `next_run_at`; the next tick after release fires the job
  once).
- `...::test_run_reservation_waits_out_a_claim_holding_the_lock` (`try_register_running_job` under a 1.5 s holder).
- `...::test_an_unwritable_fence_root_still_fails_open`
- `tests/hermes_cli/test_admission_fence.py::test_reserve_and_extend_default_to_one_short_try_and_accept_a_longer_timeout`

The commit message reports that these failed before the fix and pass with it. The changed test files passed in the targeted Linux source run. The tests require a platform with `fcntl` (`SUPPORTED`).

### Applying

Use the full-series recipe. The patch builds on the admission-fence and cron-hold work documented in
`core/update-admission-fence.md` and on the hub CPU-cap claim hold in `core/kanban-hub-cpu-cap-gate.md`.

### Gotchas

- A tick can now block for up to `BUSY_WAIT_S` (about 2.75 s). That is acceptable for the lock-free cron tick, but
  callers that hold the session-map lock must keep the 50 ms default.
- Keep the `except AdmissionBusy` clause before `except (OSError, TimeoutError)`. If the order is reversed, the
  fail-open behavior returns without any visible error.

### Plugin

None.

---

## 1927: Gateway ACP shutdown bookkeeping is bounded and off the owner loop

### Behavior

At gateway stop, `settle_gateway_runtime` ends every ACP session nobody is viewing. That sweep used to run
synchronously on the owner event loop, so a slow SQLite read or receipt lookup could hold the loop and run past the
stop budget. Now the sweep shares one aggregate budget across all profiles: at most 2 s, or less if less of the stop
budget remains. It runs in the gateway-owned executor, not the loop's default executor. When the budget runs out,
the sweep logs `ACP shutdown bookkeeping budget exhausted; left for recovery` and the stop sequence continues.
A failure in one profile's store is logged and does not abort the stop.

### How it works

- `end_idle_acp_sessions(authority, deadline=None)` checks a monotonic deadline before it opens the read
  context, during the row scan, and before each per-session end, and starts no new I/O after the deadline passes.
- The new `settle_idle_acp_sessions(runner, authorities, remaining)` computes one deadline. For each authority it
  awaits `runner._run_in_executor_with_context(end_idle_acp_sessions, authority, deadline)` under
  `asyncio.wait_for` with the time left.
- Cancelling the await cannot stop a SQLite call that is already running. The physical worker stays visible to
  executor quiesce (`_shutdown_executor` returns its count), so the shutdown DB-close gate leaves handles open
  rather than closing them under a live reader. The default executor is avoided because `asyncio.run` would wait on
  it again at process exit.

### Code

- `gateway/session_acp_lifecycle.py`: `end_idle_acp_sessions`, `settle_idle_acp_sessions`.
- `gateway/run_runtime.py`: `settle_gateway_runtime` calls `settle_idle_acp_sessions`.
- Tests: `tests/gateway/test_acp_shutdown_budget.py` (new), `tests/acp_adapter/test_gateway_acp_session_end.py`
  (the duck runner now uses the real owned executor and shuts it down).

### Tests

- `tests/gateway/test_acp_shutdown_budget.py::test_slow_receipt_does_not_block_settlement` (a wedged receipt: the
  settle finishes in under 2 s, the ticket store is still revoked, and `_shutdown_executor(drain_timeout=0) == 1`
  reports the live worker).
- `...::test_sweep_stops_before_next_receipt_after_deadline`
- `...::test_budget_is_shared_by_profiles`
- `...::test_exhausted_settle_budget_skips_bookkeeping`
- `tests/acp_adapter/test_gateway_acp_session_end.py::test_last_viewer_or_shutdown_ends_only_idle_acp_sessions`
  and `::test_acp_session_left_mid_turn_ends_once_its_fifo_drains` (adjusted to the owned executor).

The changed test file(s) passed in the targeted Linux source run. A real gateway process stop is untested.

### Applying

Use the full-series recipe. The `settle_gateway_runtime` hunk replaces a loop that earlier patches added. It uses
`authorities` and `deadline`, which already exist in that function in the replayed tree.

### Gotchas

- Sessions left over when the budget runs out are left for later recovery; they are not ended at that stop.
- Test runners modeled as a `SimpleNamespace` need `_get_executor` and `_run_in_executor_with_context`. Without
  them the sweep raises, and the error is logged rather than failing the test loudly.

### Plugin

None.

---

## 1928: Kanban sync, a reserve is retried after a reused socket drops

### Behavior

On a kept-alive hub connection, if the server closes a reused socket without answering, `_call_kept_alive` sends
the request once more on a fresh socket. Before this patch only `GET` was resent. Now the Board placement `reserve`
POST is resent too, because it is fenced by `expected_revision`: replaying the identical body cannot apply twice.
Other POSTs, such as ops and leases, are still never resent after a lost answer. The resend happens only for a
reused socket, only on the first attempt, and with the remaining request budget recomputed.

### How it works

`Hub._call` and `_call_kept_alive` gain a keyword-only `replay_safe` argument (default False). The retry condition
becomes `reused and attempt == 0 and (method == "GET" or replay_safe)`. Only `Hub.reserve` passes
`replay_safe=True`.

### Code

- `hermes_cli/kanban_sync_client.py` (`Hub._call`, `Hub._call_kept_alive`, `Hub.reserve`).
- `tests/hermes_cli/test_kanban_sync_hub_keepalive.py` (adds a `drop_once` server mode and a `dropped` event).

### Tests

In `tests/hermes_cli/test_kanban_sync_hub_keepalive.py`:

- `test_get_resend_recomputes_remaining_budget[...-reserve=True|False]` (now also parametrized over reserve)
- `test_stale_socket_reserve_is_retried_once`
- `test_reserve_lost_answer_retry_is_bounded[warm|cold]` (a warm socket allows 2 POSTs, a cold socket 1)
- `test_reserve_lost_answer_replays_identical_body`
- `test_lease_whose_answer_is_lost_is_not_resent`
- the existing `test_post_whose_answer_is_lost_is_not_resent` remains

The changed test file(s) passed in the targeted Linux source run. These tests use a local threaded HTTP server; a real hub is untested.

### Applying

Use the full-series recipe.

### Gotchas

- Opt a POST into `replay_safe` only if the server rejects a duplicate, for example through a revision fence. A
  lease or op replay could apply twice.
- Proxied connections and non-HTTP(S) URLs go through `_call_urlopen`, which has no such resend.

### Plugin

None.

---

## 1929: A Desktop terminal batch timeout stops the batch, not the turn

### Behavior

In a Desktop session, a message with two or more terminal calls is prepared as a batch. Before this patch, when one
command passed the sequential deadline, the guard called `agent.interrupt(tool_reason="terminal batch timeout")` and
the turn ended with no assistant reply. A timed-out batch preparation did the same. Now both timeouts stop only the
batch:

- The timed-out call gets its normal timeout result.
- Every call that has not started gets a not-run row: `COMMAND_TIMEOUT_NOTICE` after a command timeout, or
  `PREPARATION_TIMEOUT_NOTICE` after a preparation timeout, with `{name}` replaced by the tool name.
- The turn continues to the next model call, as it does outside a batch.
- Later prepared commands still never start.
- A user stop keeps its attribution, message and redirect.

### How it works

- `_TerminalBatch.stop(notice)` records `stop_notice` and calls `close()` straight away, which releases slots and
  cancels pending approvals.
- In `_run_sequential_tool_execution_middleware`, the timeout branch calls
  `prepared.batch.stop(COMMAND_TIMEOUT_NOTICE)` instead of interrupting. Other states still `close()`.
- In `terminal_approval_batch`, a `TimeoutError` from `batch.start()` calls `stop(PREPARATION_TIMEOUT_NOTICE)`. A
  cancellation still only closes.
- `_execute_tool_calls_sequential` calls `report_batch_stop` before each call. Once the batch has stopped itself,
  that function appends one skipped row per remaining call through `_append_skipped_tool_results` and breaks.
  If a flush fails, it returns.

### Code

- `agent/terminal_approval_batch.py` (`stop_notice`, `stop`, `COMMAND_TIMEOUT_NOTICE`,
  `PREPARATION_TIMEOUT_NOTICE`, `batch_stop_notice`, `report_batch_stop`).
- `agent/tool_executor.py` (the timeout branch and the sequential loop).
- Tests: `tests/agent/test_terminal_batch_timeout_continues.py` (new),
  `tests/agent/test_interrupt_issuer_attribution.py` (updated).

### Tests

- `tests/agent/test_terminal_batch_timeout_continues.py::test_batch_timeout_reports_to_the_model_and_the_turn_replies`:
  a real Desktop-platform `run_conversation` with three terminal calls and a 3 s deadline. The second call
  sleeps; the turn's final response is the model's second reply, there are two model calls, and the third command
  never runs.
- `...::test_stop_closes_the_batch_at_once`
- `...::test_preparation_timeout_reports_every_call_and_the_turn_replies`
- `tests/agent/test_interrupt_issuer_attribution.py::test_soft_interrupt_with_tool_reason_is_attributed_to_the_system`
  (updated: a batch timeout sets no interrupt and no issuer; the system-stop skip wording is now exercised with a
  different `tool_reason`).

The commit message reports mutant and suite runs. The changed test file(s) passed in the targeted Linux source run. The tests drive the
agent loop in-process with a mocked model client; a live Desktop UI turn is untested.

### Applying

Use the full-series recipe.

### Gotchas

- The `terminal_batch_timeout` and `terminal_batch_preparation_timeout` interrupt issuers are no longer produced.
  Code or dashboards keyed on them will see nothing.
- Notices are rendered with `str.replace("{name}", ...)`, not `str.format`.

### Plugin

None.

---

## 1930–1931: Redaction, random-token secret fields and subprocess argv

### Behavior

Terminal and `execute_code` output is redacted with `code_file=True`, which is source-preserving and used to skip
every key-based pass. Because of that, a generated credential such as a managed-worker adoption secret
(`secrets.token_urlsafe(32)`, stored in `worker-outboxes/*/pending.json` as `scope.secret`) could reach transcripts
three ways: a printed Python repr, the raw JSON journal, or a `TimeoutExpired` / `CalledProcessError` message that
echoes an argv carrying the secret. With 1930:

- In `code_file` mode, a credential-named JSON field, escaped-JSON field or Python-repr field is masked when its value
  looks like a random token: 32 or more base64url characters mixing upper and lower case, not starting with `/`.
  Fixture-shaped values, hex digests and non-secret keys such as `session_id` or `token_count` are left alone.
  `file_read` mode uses the non-reusable sentinel; other modes use `***`.
- In every mode, random tokens between `Command '` and its `' timed out after ` / `' returned non-zero exit status ` /
  `' died with ` tail are masked. This covers `--token=S`, `KEY=S`, multi-line shell scripts and very long argv
  lists. Ordinary argv, including paths and 40-hex SHAs, is left alone.
- `_JSON_FIELD_ESCAPED_RE` now starts a match only at the head of a run of backslashes, so it stays linear on long
  runs.
- The user guide now says never to pass credentials in argv.

1931 changes only a test: the terminal entry-path probe now quotes the interpreter path and the script path with
`shlex.quote`, so it works when `sys.executable` contains spaces.

### How it works

- `_RANDOM_TOKEN_RE` and `_looks_like_random_token` decide whether a value looks generated.
- `_redact_random_secret_fields` runs `_JSON_FIELD_RE`, `_JSON_FIELD_ESCAPED_RE` and `_PYTHON_REPR_FIELD_RE` with
  callbacks that mask only random-token values. For repr fields it also requires `_is_python_repr_secret_key`.
  `redact_sensitive_text` calls it in the `code_file` branch when the text contains `:`.
- `_redact_subprocess_argv` finds each diagnostic tail, then searches backward, bounded by
  `_SUBPROCESS_COMMAND_SPAN` (16384), for the `Command <quote>` head. It masks `_ARGV_RANDOM_TOKEN_RE` matches in the
  span between them. A `scanned` watermark keeps the backward windows from overlapping, so the whole pass is linear.
  It runs whenever the text contains `Command `.

### Code

- `agent/redact.py`
- `website/docs/user-guide/configuration.md` (`redact_secrets` paragraph)
- `tests/agent/test_redact_outbox_secrets.py` (new in 1930; one-line quoting fix in 1931)

### Tests

All in `tests/agent/test_redact_outbox_secrets.py`, using synthetic secrets only:

- `TestSourcePreservingOutput`: `test_scope_repr_is_masked`, `test_journal_json_is_masked`,
  `test_pretty_printed_journal_is_masked`, `test_journal_inside_json_string_is_masked`,
  `test_file_read_uses_non_reusable_sentinel`, `test_every_token_urlsafe_shape_is_masked` (300 random samples),
  `test_source_fixtures_and_non_secret_keys_are_preserved`.
- `TestSubprocessArgvDiagnostics`: `test_timeout_expired_argv_list_is_masked`,
  `test_timeout_expired_shell_string_is_masked`, `test_called_process_error_argv_is_masked`,
  `test_traceback_final_line_is_masked_without_force` (through `redact_terminal_output`),
  `test_joined_multiline_and_long_argv_are_masked` (flag-eq, form-eq, env-eq, multi-line-shell, long-argv),
  `test_escaped_json_backslash_run_stays_linear`, `test_execute_code_remote_output_pipeline` (through `_clean_output`),
  `test_two_diagnostics_on_one_line_are_both_masked`, `test_hostile_output_stays_linear` (heads, head-tail-pairs,
  one-long-run; each under 2 s), `test_ordinary_argv_is_preserved`.
- `TestToolEntryPaths` (Linux and macOS only, `child_env` fixture): `test_terminal_cat_journal`,
  `test_terminal_python[repr|timeout]` (the test 1931 changed), `test_execute_code[repr|timeout]`.

These cover two entry paths: the real `terminal_tool` and the real `execute_code` (in project mode). Both entry paths passed in the targeted Linux source run. `read_file` and `search_files` are covered only through the `file_read` unit case, not
through the tools themselves, so those tool paths are untested here.

### Applying

Use the full-series recipe, and apply 1931 after 1930: it patches the test file that 1930 creates.

### Gotchas

- Detection is by the token's shape. A random-looking token in one case only, shorter than 32 characters, or
  starting with `/` is not masked. Argv masking applies only inside the exact subprocess diagnostic forms listed
  above.
- The argv rule is a backstop. Pass secrets on stdin, in an environment variable or in a `0600` file. Argv is also
  visible in `ps` and `/proc`.
- Don't copy the synthetic secret literal from the test into docs: secret scanners can flag it.

### Plugin

None.

---

## 1932: Kanban accepts the `exact_head` review metadata alias

### Behavior

A parked card waiting on a dependency review resumes once that review completes, provided the review records an exact
40-hex head. Until now the head was accepted only under `reviewed_sha` or `head`. Now `exact_head`, the spelling a
review producer emits, is accepted as well, both in the local respawn guard and in the remote dependency-review
notice check. Values that are not exactly 40 hex characters are still refused, including `None`, numbers, lists, the
empty string, 39 or 41 characters, non-hex characters and a trailing newline. A review that names a different PR
still does not cover the captured PR. The refusal text now reads
`exact_head refused (reviewed_sha/head/exact_head must be 40 hex)`.

### How it works

`_exact_head` in `kanban_dependency_review.py` checks the keys `("reviewed_sha", "head", "exact_head")` against
the `_SHA` full-match pattern. `kanban_dependency_review_notice._refusal` calls the same helper, and only its
message changes.

### Code

- `hermes_cli/kanban_dependency_review.py` (`_exact_head`, module docstring).
- `hermes_cli/kanban_dependency_review_notice.py` (refusal text).
- Tests: `tests/hermes_cli/test_kanban_dependency_review_exact_head_alias.py` (new),
  `tests/hermes_cli/test_kanban_dependency_review_sync.py` (the `key` parametrization gains `exact_head`).

### Tests

- `tests/hermes_cli/test_kanban_dependency_review_exact_head_alias.py::test_exact_head_alias_resumes_once[APPROVE|REQUEST_CHANGES|observations]`
  (the guard clears once, the claim consumes it, and the guard returns `active_pr` again).
- `...::test_malformed_alias_is_not_exact[...]` (eight malformed values).
- `...::test_alias_does_not_cover_another_pr`
- `tests/hermes_cli/test_kanban_dependency_review_sync.py::test_sync_remap_preserves_one_review_continuation[exact_head-...]`
  and `::test_local_change_request_accepts_either_exact_head_key[exact_head]`.

The changed test file(s) passed in the targeted Linux source run. The new test file imports the `board`, `parked`, `finish_reviews`, `PR` and
`HEAD` helpers from `tests/hermes_cli/test_kanban_dependency_review.py`. The notice path's new refusal text is not
asserted by any named test.

### Applying

Use the full-series recipe. This patch extends the feature documented in
`core/kanban-dependency-review-continuation.md`.

### Gotchas

- All three keys are treated the same. A review that sets any one of them to a valid SHA counts as exact-head, even
  if another key holds a malformed value.

### Plugin

None.
