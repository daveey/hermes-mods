# Mirror 2010: feature changes in patches 2006–2010

This documents the five patch bodies after the 2005 snapshot, through frozen
source tip `79e38bd675dc56816606cf0736123bbf45dacfdd`. They describe four
features: 2008 and 2009 are one feature, a code change and its test-fixture
follow-up. Paths are relative to the Hermes **source** tree unless prefixed
`core/`.

| Ordinal | Source SHA | Kind | Feature |
| --- | --- | --- | --- |
| 2006 | `33a9e561246b797a1fc9d3c2dd749aaf0f35c114` | code + tests | [Post-run Kanban tool fence](#post-run-kanban-tool-fence) |
| 2007 | `1d3fe77e9a6ecee4323ef42a1be70714913e83d2` | code + tests | [Transient Board failures debounced; timed-out reserve replayed once](#transient-board-failures-debounced-timed-out-reserve-replayed-once) |
| 2008 | `ddf4a0fe8f3c19f3cb3848f0a41463779dd7268d` | code + tests | [Replica read memo keyed on heartbeat verdicts](#replica-read-memo-keyed-on-heartbeat-verdicts) |
| 2009 | `7cc837ce79f4f3048724ff0763b321d675afc363` | test-only | same feature as 2008 (fixture module alias) |
| 2010 | `79e38bd675dc56816606cf0736123bbf45dacfdd` | code + tests | [API server publishes its bound port](#api-server-publishes-its-bound-port) |

**Verification status.** This document describes patch contents only. It
does not report a test run, a replay or a tree comparison. Those belong in the
2010 mirror verification report. The commit messages quote counts and
measurements from the source session (for example the post-run replay over a
production state database and the 46 MB re-read figure). They are quoted as
the commits' motivation and are not reproduced here. Nothing here is a live
performance measurement: no latency or p95 claim is made for any of these
patches.

---

## Post-run Kanban tool fence

**Patch 2006** (`33a9e561246b797a1fc9d3c2dd749aaf0f35c114`). Extends the
worker fences in [kanban-worker-fence-refusal.md](kanban-worker-fence-refusal.md)
and the unlink tool in [kanban-unlink.md](kanban-unlink.md).

**Behavior.** A dispatcher worker's run ends at its first successful
park or handoff (block, schedule, complete, continue, request_review), but the
worker process keeps running until its turn ends. Before this patch:

- the success payload did not say the run was over, so the worker kept
  calling tools;
- `kanban_link` / `kanban_unlink` did not check the run, so a worker could undo
  its own park. In the source incident, a worker parked with
  `kanban_block(kind='dependency')` and then unlinked that parent, which
  promoted the card back to `ready`;
- the next lifecycle call was refused by the run-ownership CAS with "unknown
  id or not in running/ready/triage", which hid the real cause.

Now, for a worker that can name its own run:

- `kanban_block`, `kanban_schedule`, `kanban_continue`, `kanban_move`,
  `kanban_request_review` and `kanban_request_changes` are refused up front
  once the run has ended. The error has `code=run_already_ended`, `task_id`,
  `run_id`, `run_outcome`, the card's current `status` and
  `handoff_complete=false`, and its text says "Nothing changed" plus the
  next-step hint. For `kanban_schedule` the refusal also appends the existing
  human-answer hint (`kanban_reason_comment.human_answer_refusal`), so that way
  out stays visible.
- `kanban_complete` is exempt and keeps its existing own-park close
  (`parked_by_own_run`).
- Every successful run-lifecycle result that ended the caller's own run gains
  `run_ended: true` and `next: "This run is over; the card is <status>. ..."`.
  An existing `next` (for example `kanban_continue`'s) is kept in front.
- `kanban_link` is refused when either end of the edge is the worker's own card
  and its run has ended. `kanban_unlink` is refused for an ended-run worker.
  Links between two other cards (for example cards the worker created) are
  unchanged.
- Orchestrators (no `HERMES_KANBAN_TASK`), live workers, and workers without a
  parseable `HERMES_KANBAN_RUN_ID` are unaffected. The CLI is unaffected.
- The `kanban_block` schema now says it ends the run; the `kanban_unlink`
  schema says it is refused once the run has ended.

**How it works.** New module `tools/kanban_tools_run_end.py` (kept separate
because `kanban_tools.py` is over its line cap):

- `ended_run(conn, task_id)` reads the `task_runs` row for
  `HERMES_KANBAN_RUN_ID` and `HERMES_KANBAN_TASK`. It returns
  `{task_id, run_id, outcome, status}` only when `ended_at` is set. A
  `task_id` other than the env task returns `None`.
- `refusal(conn, tool_name, task_id)` builds the `run_already_ended` error.
- `guard_refusal` is called from `_worker_guard` for every tool in
  `_RUN_LIFECYCLE_TOOLS` except `kanban_complete`. It opens the board only
  when `bound_worker()` is true. `_Reject` gains a `payload=` keyword so the
  structured error is returned as-is.
- In `_kanban_handler`, a result of a `_RUN_LIFECYCLE_TOOLS` call that starts
  with `{"ok": true` goes through `annotate_success`. That reads the run only
  for a bound worker. The transition has already committed, so a
  `sqlite3.Error`, `OSError` or `ValueError` during the read returns the
  original success unchanged (logged at debug).
- `tools/kanban_tools_graph.py`: `handle_link` checks the fence only when the
  env task is the parent or the child; `handle_unlink` checks it for every
  call (a no-op unless the caller is a bound worker whose run ended).

**Code.** `tools/kanban_tools_run_end.py` (new), `tools/kanban_tools.py`,
`tools/kanban_tools_graph.py`, `tools/kanban_tools_schemas.py`.

**Tests.** New file `tests/tools/test_kanban_post_run_calls.py`. It calls the
wrapped in-process tool handlers (`tools.kanban_tools._handle_*`) with worker
env vars set. It covers:

- block success carries `run_ended`/`next`;
- unlink and link of the own card after a dependency park are refused and
  change nothing;
- block(time), schedule, continue, request_review and request_changes after
  the park name the ended run (parametrized);
- the incident sequence end to end (unlink, block, schedule after the park);
- the refusal reports the card's current status after a successor run;
- schedule, block, complete, continue and request_review successes signal
  `run_ended` with the right status (parametrized);
- a live worker can link and unlink its own card; heartbeat has no
  `run_ended`;
- an orchestrator can edit a parked card's edges, and its park of a running
  card has no `run_ended`;
- the worker can still complete its own blocked card;
- continue keeps its own `next` and adds `run_ended`;
- no parseable run id leaves graph tools as before; another task's scope is
  ignored;
- a parked worker may link two other cards, but not its own card as a parent;
- a failed annotation read keeps the committed success;
- a non-worker lifecycle call skips the run-end reads.

Updated contracts: `tests/tools/test_kanban_tools.py`
(`test_schedule_parks_current_worker_with_reason` expects the new fields;
`test_worker_blocks_on_dependency_then_unlinks_and_completes` becomes
`test_worker_links_then_unlinks_while_live_and_completes`;
`test_old_run_cannot_complete_once_the_card_was_reclaimed` unlinks as an
orchestrator) and `tests/hermes_cli/test_kanban_refused_op_comments.py`
(`test_tool_refused_schedule_leaves_no_comment_and_says_why` expects
`run_already_ended`).

**Untested paths.** The `kanban_move` fence and its `run_ended` annotation have
no test. All cases call the Python handlers directly: the model-facing
`tool_call` / registry dispatch path and `execute_code` calls are untested
here. The `kanban_schedule` human-answer hint inside the refusal has no
dedicated assertion.

**Gotchas.** The fence depends on two env vars only. A worker started without
a parseable `HERMES_KANBAN_RUN_ID` keeps the old behavior, including the
misleading CAS refusal. Unlinking a dependency parent is now possible only
while the run is live; after a park, an orchestrator or the board supervisor
has to do it. `kanban_complete` stays callable after the run ended by design.

**Plugin boundary.** Core. The fence sits in the tool wrapper and graph
handlers, before any board write; a plugin hook could not distinguish the
caller's own ended run from the CAS refusal.

## Transient Board failures debounced; timed-out reserve replayed once

**Patch 2007** (`1d3fe77e9a6ecee4323ef42a1be70714913e83d2`). Extends
[kanban-queue-reasons-repin.md](kanban-queue-reasons-repin.md),
[kanban-pull-attempt-timing.md](kanban-pull-attempt-timing.md) and the client
in [kanban-central-sync-client.md](kanban-central-sync-client.md).

**Behavior.**

- *Debounce.* A single reserve or feed-pull timeout between two successful
  passes used to record `board_error` on the card and then the previous queue
  reason again, posting a worker-placement comment pair each time. Now a
  transient failure (transport `OSError`, `SyncUnavailable`, or an HTTP 5xx
  `HubError`) changes the card's `worker_queue_reason`, and posts its comment,
  only on the `CONFIRM_PASSES` = 2nd consecutive transient failure of the
  same request kind for that card.
  - Kind `pull` (the dispatcher's feed pull, observed for every origin card
    each tick) ends on a successful pull or a feed `Resync`.
  - Kind `card` (that card's own reserve or repin) ends only on a Board answer
    for that card: a reply, a refusal (including `revision_conflict`), a
    successful repin, or a pickup by the reserved worker. A card that the
    batch cursor skips neither continues nor breaks its streak.
  - Any mix of transient errors continues a streak (502 then 503 is one
    outage). A streak also ends if the card ran since its last failure.
  - There is no wall-clock window: with many queued cards a card may be retried
    only every few ticks.
  - Every unconfirmed failure is appended as a `worker_queue_board_error`
    event with the same allowlisted provenance (stage, transport, `budget_ms`,
    `elapsed_ms`, `http_status`) and its `pass` number, under the same
    ownership fence, with no comment and no queue-reason change.
  - Board refusals and non-5xx `HubError`s still record at once.
- *Reserve replay.* A replay-safe request (only reserve opts in: it is
  revision-fenced and the Board answers a same-revision replay with the
  recorded reservation) that times out on its first attempt is resent once on
  a fresh connection, if at least 0.1 s of the shared deadline remains. Ops
  and leases are never replayed. On the dispatcher's 2 s tick the per-call
  timeout equals the remaining deadline, so the replay mostly helps callers
  with a longer deadline; the debounce is what stops the dispatcher's flip.

**How it works.**

- `hermes_cli/kanban_queue_reason.py`: the owner check moves into
  `_owned_row`, shared by `_record` and the new `_record_diagnostic`.
  `record_error` classifies with `_transient`; for a transient failure it
  counts `task_runs` for the card and calls `_bump_streak(gateway, task_id,
  stage, runs)`. Below `CONFIRM_PASSES` it writes the diagnostic and returns
  `False`. The registry `_streaks` maps `(gateway, task, kind)` to
  `(passes, run count at the last failure)`, is capped at 4096 entries and is
  guarded by a `threading.Lock` (the sync loop and the dispatcher both reach
  it). It is process-local: a restart can delay a confirmation by one pass,
  never manufacture one. `clear_streak` and `clear_pull_streaks` end streaks;
  `record_reply` and `record_pull_error(Resync)` call them.
- `hermes_cli/kanban_worker_placement.py`: `reevaluate` clears the card streak
  after a repin reply and after a detected pickup; `dispatch_tick` clears pull
  streaks after a successful pull.
- `hermes_cli/kanban_sync_client_attempt.py`: new `replay_after_timeout(exc,
  attempt, replay_safe, deadline, now)` returns true only for a
  `TimeoutError` on attempt 0 of a `replay_safe` request with at least
  `REPLAY_MIN_BUDGET_S` (0.1 s) left; it logs a warning.
  `hermes_cli/kanban_sync_client.py` (`Hub._call_kept_alive`) consults it in
  its send, response and body-read `except` branches and `continue`s on a
  fresh connection.

**Code.** `hermes_cli/kanban_queue_reason.py`, `hermes_cli/kanban_sync_client.py`,
`hermes_cli/kanban_sync_client_attempt.py`, `hermes_cli/kanban_worker_placement.py`.

**Tests.** New file `tests/hermes_cli/test_kanban_queue_transient_debounce.py`,
against a real kept-alive local HTTP Board through `reevaluate` and
`dispatch_tick`, plus direct `record_error` / `record_pull_error` calls:

- a single reserve timeout between successes does not flip or comment; two
  consecutive ones flip once with one comment pair; a success between failures
  resets the streak;
- a single pull timeout fan-out does not flip; two consecutive pull timeouts
  flip each card once;
- isolated reserve and pull failures do not combine; a sustained reserve outage
  flips while the feed is healthy; an alternating 502/503 outage still
  confirms; a card retried only every few ticks still confirms;
- the diagnostic never stores raw error text and respects the ownership
  fence; HTTP 5xx is debounced but a Board refusal records at once;
- a run since the last failure, and a feed reset, end streaks; a non-transient
  pull error does not end a card streak;
- reserve timeout replays once with an identical body, is bounded to one,
  is not replayed without remaining budget, and replays within a larger shared
  deadline; leases and ops POST timeouts are not replayed.

`tests/hermes_cli/conftest.py` gives each test a fresh `_streaks` registry.
Five existing provenance/timing files pin `CONFIRM_PASSES = 1` with an autouse
fixture, because they check the recorded `board_error` fields, not the
debounce: `test_kanban_pull_attempt_timing.py`,
`test_kanban_pull_attempt_timing_edges.py`,
`test_kanban_queue_error_provenance.py`, `test_kanban_queue_error_timing.py`,
`test_kanban_queued_reserve_throttle.py` (all under `tests/hermes_cli/`).

**Untested paths.** The test Board's "hang" action times out while waiting for
the response; the send-time and body-read replay branches have no dedicated
test. The streak clears after a repin reply and after a pickup detection in
`reevaluate` are not named by any test. A real Board outage is untested here.

**Gotchas.** The first transient failure of an outage is now visible only as a
`worker_queue_board_error` event, not as a queue reason or comment. Tests that
assert a recorded `board_error` after one transient failure must pin
`CONFIRM_PASSES = 1` or fail twice. The replay can at most double one reserve's
time inside the same deadline; it never extends the deadline.

**Plugin boundary.** Core. The debounce lives inside the queue-reason write
transaction and the replay inside the Board HTTP client; a plugin cannot sit
between a failed attempt and its recorded reason.

## Replica read memo keyed on heartbeat verdicts

**Patches 2008** (`ddf4a0fe8f3c19f3cb3848f0a41463779dd7268d`) **and 2009**
(`7cc837ce79f4f3048724ff0763b321d675afc363`, test-only). Extends
[kanban-replica-read-memo.md](kanban-replica-read-memo.md).

**Behavior.** The owner collector rewrites `gateway/info` with a new
`observed_at` every cycle. The live replica memo key used to contain
`tuple(info_rows)`, so each of those writes forced a full task-row read and
projection. The projection uses the info rows only through scope-heartbeat
freshness verdicts (owner terminal release and moved evidence `live_scopes`).
The key now carries the set of scopes that are fresh at the read's own `now`
instead of the raw rows. So:

- a heartbeat refresh that changes only `observed_at`, with the same set of
  fresh scopes, serves the memo;
- a scope that becomes fresh or expires, a new fresh scope, and an invalid,
  duplicate, non-`ok` or identity-mismatched scope (each is absent from the
  verdict set) change the key and force a re-read;
- the old `valid_until` cut-off (next heartbeat edge) is gone; the live verdict
  in the key replaces it. `read_at <= now` is still required, so a clock step
  back re-reads. Hub staleness and per-card `synced_at` are still compared as
  before.

Patch 2009 changes how the new heartbeat test module gets the shared `hub`
fixture: `hub = memo_helpers.hub` (module alias) instead of
`from ... import hub`. The fixture is the same; the commit gives no further
reason.

**How it works.** `hermes_cli/kanban_scope_heartbeat.py` adds
`fresh_scopes(heartbeats, now)`: the `scope_heartbeats` keys for which
`fresh(beat, now, window)` holds, as a `frozenset`. In
`hermes_cli/kanban_session_activity.py`, `_read_remote_links` builds its key
with `_scope_hb.fresh_scopes(_scope_heartbeats(info_rows), now)`, drops the
`now < memo[2]` check, and stores `None` in the memo's former `valid_until`
slot.

**Code.** `hermes_cli/kanban_scope_heartbeat.py`,
`hermes_cli/kanban_session_activity.py`.

**Tests.** New file
`tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py` (2008; its
fixture import changed in 2009). Each case compares the live
`_read_remote_links(path)` with an explicit-`now` read at the same instant and
counts row reads:

- a timestamp-only heartbeat refresh serves the memo; a refreshed heartbeat
  extends reuse past the first cut-off;
- expiry flips release exactly after the window; a future-dated heartbeat
  proves nothing until it is reached;
- clock rollback re-reads;
- invalid or duplicate scopes re-read and lose release (parametrized);
- an unrelated scope or gateway row changes nothing; a new fresh scope
  re-reads;
- a task mutation alongside a heartbeat refresh re-reads; a gateway identity
  change re-reads;
- concurrent reads during heartbeat churn answer exactly.

`tests/hermes_cli/test_kanban_replica_read_memo.py::test_meta_and_heartbeat_changes_reread`
now inserts a heartbeat row with a fresh `ok` scope, since an empty-scope row
no longer changes the key.

**Untested paths.** The consumers (sidebar / activity APIs that call
`_read_remote_links`) are not exercised by these tests. A live owner
collector is untested here; no CPU or latency effect is measured by this
mirror.

**Gotchas.** The key now depends on `now`, so two reads in the same second can
differ only when a verdict flips between them, which is the intended re-read.
`kanban-replica-read-memo.md` still describes the per-heartbeat `beat` /
`beat + SCOPE_HEARTBEAT_FRESH_S` cut-off; with 2008 that bullet is superseded
by this section.

**Plugin boundary.** Core read path; no plugin equivalent.

## API server publishes its bound port

**Patch 2010** (`79e38bd675dc56816606cf0736123bbf45dacfdd`).

**Behavior.** With `platforms.api_server.port: 0` (or `API_SERVER_PORT=0`)
the kernel picks the port. Before this patch the adapter kept `_port = 0`, so
the connected listener base, the runtime status payload and the
`API server listening on http://<host>:<port>` log line all said `:0`. Now
they report the port the listener actually owns. A fixed nonzero port is
unchanged.

The crash-recovery test no longer reserves a port by binding and closing a
socket in the parent. That left a gap in which another listener (in the probe,
a dashboard taking the released port on restart) could claim the port between
daemon restarts. Each daemon now binds port 0 itself and the test reads its
URL from that daemon's own log line.

**How it works.** In `APIServerAdapter.connect`
(`gateway/platforms/api_server.py`), after the site starts and before
`_mark_connected`, the patch sets `self._port = self._runner.addresses[0][1]`.
`_mark_connected(listener_base=...)`, `_publish_runtime_status()` and the
listening log line then use that value. A duplicated comment above the
API-key startup guard is removed (no behavior change).

**Code.** `gateway/platforms/api_server.py`.

**Tests.**

- New `tests/gateway/test_api_server_bound_port.py::test_ephemeral_listener_reports_bound_port`:
  an in-process adapter with port 0 connects; the runner's port is nonzero,
  `_api_server_status_payload()['port']` equals it, the log line names it, and
  `/health` on it returns 200.
- `tests/gateway/test_api_crash_recovery.py::test_api_crash_queue_and_identified_result_retry`:
  config and `API_SERVER_PORT` are `0`; after each daemon start the test waits
  (up to 120 s) for the new log line past the previous log offset and uses
  that URL.
- New `tests/gateway/test_api_crash_collision_probe.py::test_dashboard_takes_released_port`:
  wraps `subprocess.Popen` so the second daemon start runs the gateway control
  API on the previously logged API port, then runs the crash-recovery
  exercise, which must still pass.

**Untested paths.** A host that resolves to several addresses publishes only
`addresses[0]`; that case and IPv6 hosts are untested. The `API_SERVER_PORT=0`
env path is exercised only together with the config value inside the
crash-recovery daemon test, not on its own.

**Gotchas.** An empty `addresses` list would raise inside `connect`'s outer
`try` and be logged as "Failed to start API server". Clients that discover the
API server must read the published port (status payload or log), not the
configured `0`.

**Plugin boundary.** Core platform adapter; no plugin equivalent.

---

## Test-file inventory

| Ordinal | Test files (changed or new) | Production code |
| --- | --- | --- |
| 2006 | `tests/tools/test_kanban_post_run_calls.py` (new), `tests/tools/test_kanban_tools.py`, `tests/hermes_cli/test_kanban_refused_op_comments.py` | `tools/kanban_tools_run_end.py` (new), `tools/kanban_tools.py`, `tools/kanban_tools_graph.py`, `tools/kanban_tools_schemas.py` |
| 2007 | `tests/hermes_cli/test_kanban_queue_transient_debounce.py` (new), `tests/hermes_cli/conftest.py`, `test_kanban_pull_attempt_timing.py`, `test_kanban_pull_attempt_timing_edges.py`, `test_kanban_queue_error_provenance.py`, `test_kanban_queue_error_timing.py`, `test_kanban_queued_reserve_throttle.py` | `hermes_cli/kanban_queue_reason.py`, `kanban_sync_client.py`, `kanban_sync_client_attempt.py`, `kanban_worker_placement.py` |
| 2008 | `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py` (new), `tests/hermes_cli/test_kanban_replica_read_memo.py` | `hermes_cli/kanban_scope_heartbeat.py`, `hermes_cli/kanban_session_activity.py` |
| 2009 | `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py` | none |
| 2010 | `tests/gateway/test_api_server_bound_port.py` (new), `tests/gateway/test_api_crash_collision_probe.py` (new), `tests/gateway/test_api_crash_recovery.py` | `gateway/platforms/api_server.py` |

## Applying

These patches extend the curated mirror through 2005. They are not standalone
plugin packages or a safe cherry-pick menu: 2007's test changes assume 2006's
tree, 2009 edits a file 2008 creates.

The source range `221510a25b32673c6f678f383ce15201b6f1c444..79e38bd675dc56816606cf0736123bbf45dacfdd`
is linear (no merges), so the 2010 pass needs no new bridge. Its policy and
replay plan are copies of the 2005 ones with `source_tip` set to the frozen
tip. Verify with the unchanged verifier in a disposable environment whose
source clone holds the frozen tip, using the upstream exclusion SHA from the
policy:

```sh
python3 -B core/verify_mirror.py \
  --source-repo <source-clone> \
  --source-tip 79e38bd675dc56816606cf0736123bbf45dacfdd \
  --base 84a0301415edd6d1385207ec96e4d7f2b6c1fcd9 \
  --patch-dir core/patches \
  --policy core/mirror-policy-2010.json \
  --replay-plan core/mirror-replay-2010.json
```

A clean apply alone does not show that tests pass or that deployed behavior
works. Never regenerate a published patch to make an incomplete object store
pass.

## Gotchas and verification boundaries

- One patch is test-only (2009). 2010's test changes are mostly about test
  port ownership; its single production line affects only `port: 0`.
- The named tests show what is asserted, not that it was executed. Execution
  belongs in the 2010 verification report.
- Untested here: the `kanban_move` fence; the registry/`tool_call` and
  `execute_code` entry paths for 2006; the send-time and body-read reserve
  replay branches and the repin/pickup streak clears for 2007; replica-read
  consumers and a live owner collector for 2008; multi-address and IPv6 API
  hosts for 2010; any real Board, production dispatcher or live gateway.
- No performance claim: 2008 removes re-reads on timestamp-only heartbeat
  writes by construction, but this mirror measures no CPU, latency or p95.

## Plugin boundary

All four features are **core** changes. No new plugin, enablement or config
migration is introduced. The run-ended fence, the queue-reason debounce, the
Board client replay, the replica memo key and the API listener port all sit
at core entry points that a plugin hook cannot reach before the decision is
made.
