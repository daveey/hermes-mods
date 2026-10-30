# Kanban supervisor requests (durable worker graph-edit handoff)

Patches: 1278 1279 1280 1281

- `patches/1278-feat-kanban-durable-supervisor-requests-for-worker-g.patch`
- `patches/1279-fix-kanban-supervisor-requests-refuse-link-ops-up-fr.patch`
- `patches/1280-fix-kanban-short-first-sentence-for-kanban_superviso.patch`
- `patches/1281-docs-kanban-kanban_supervisor_request-description-ow.patch`

Source: fork PR #315 (rebase-merged; four commits). 1278 is the feature, 1279 refuses link ops up front and narrows the late wake, 1280 and 1281 only reword the tool's schema description.

## Behavior

A dispatcher worker may only remove edges of its own card (`kanban_unlink`), and the assignment-repair route only hands work to another task-scoped worker, which has the same wall. A worker that created a graph of cards and had to re-order it therefore had no route (`no_capable_supervisor` when creator and assignee profile match). This adds one.

- **Worker tool `kanban_supervisor_request(ops, reason, dry_run)`**. `ops` is a list (max 20) of `{op: 'unlink', parent_id, child_id}`. It files a durable request in the board DB; it is never a human ask or a chat turn.
- **Origin.** Only a live dispatcher worker run may file: `HERMES_KANBAN_TASK` plus `HERMES_KANBAN_RUN_ID` must match the task's current running run. An orchestrator/operator (`not_a_worker`), a stale run (`stale_run`), and a delegated child are refused.
- **Scope.** Each endpoint must be the requester's own card or a card the requester's task created (read from the card's `created` event `creator_task_id`). Cards another task created, unknown, or archived cards are `refused`.
- **Deduplication.** The same ops from the same requester while a request is still `pending`/`accepted` return the existing row (`duplicate: true`). A finished request is history; filing again is a new request.
- **`dry_run`** is a per-op capability preflight and writes nothing. Verdicts: `self` (the worker can do it itself), `supervisor` (in scope, needs the executor), `noop` (edge already in that state), `refused` (with reason, including cycles). It also reports `executor_available` (an executor seen within 300 s).
- **Refusals on a real request:** any `link` op (`link_not_requested`; link ops are preview-only, use `kanban_link` directly), any op the worker can do itself (`own_card_op`; use `kanban_unlink`), any refused op (`refused`), and a batch with nothing for a supervisor to do (`no_supervisor_needed`). All-or-nothing: one bad op files nothing.
- **Executor** (non-worker process only): accepts a request with a 120 s lease by compare-and-swap, re-checks scope against the board as it is now, applies each unlink through the ordinary `kanban_unlink` handler, and records per-op outcomes (`applied`, `already_applied`, `failed`, `not_applied`). A requester that is gone, archived or done, or a scope that no longer holds, makes the request `rejected`. An op failure makes it `failed` and later ops `not_applied`.
- **Redelivery.** A crash after acceptance leaves the row `accepted`; an expired lease, or a same-host executor pid that is gone, puts it back up. Ops are idempotent. After 5 acceptances whose executor never finished, the request is failed (`abandoned after 5 deliveries`), not retried forever.
- **Receipts.** Every transition appends a `supervisor_request_*` event on the requester's card and the terminal one adds a comment (author `kanban-supervisor`). A requester parked `scheduled` is woken once, also when it parks only after the result landed, but only if its park names the request id; an unrelated park is left alone. A result never completes, unblocks or releases the requester.
- **Dispatcher tick.** `dispatch_once` calls `dispatcher_drain` in its non-worker reclaim phase, records `(request id, status)` in the tick result, never raises, and is skipped if the process still carries worker env (logged once). Config `kanban.supervisor_requests.enabled: false` leaves requests pending.
- **Operator CLI:** `hermes kanban supervisor-requests [--drain] [--task ID] [--status pending|accepted|done|rejected|failed] [--json]` lists requests, or with `--drain` executes them as a non-worker executor.
- **Worker shell guard.** `hermes kanban unlink` from a worker's shell is refused for edges outside its own card, with a message naming `kanban_supervisor_request`, so the CLI is not a way around the tool's rule.
- **Codex runtime workers** reach the tool because it is in the Hermes tools MCP server's `EXPOSED_TOOLS`.

## How it works

- Tables created lazily by `ensure_schema`: `supervisor_requests` (requester task and run, `ops_json`, `reason`, `dedupe_key` = sha256 of requester + ops, `status`, `executor`, `lease_expires`, `attempts`, `result_json`, `error`, timestamps, `requester_woken`) and `supervisor_executors` (`executor_id` = `host:pid:role`, `last_seen`, refreshed at most once a minute).
- `preflight` simulates ops in order on the current edge set, including a reachability check for cycles, so later verdicts see the graph as earlier ops leave it.
- `drain` registers the executor, rewakes late parkers, then loops `_accept_next` (lease CAS) → `_execute` → `_finish` up to `limit` (10) requests; exceptions are recorded as a failed request.
- 1279 moves link handling from "previewed with verdict self" to an up-front refusal on real requests and requires a late wake to be for a park that names the request. 1280/1281 only shorten the first sentence of the tool's catalog line and state that own-card edges are refused.

## Code

- `hermes_cli/kanban_supervisor_requests.py` (new): schema, validation, preflight, submit, drain, receipts, CLI handler.
- `tools/kanban_tools_graph.py` (new): `link`/`unlink`/`supervisor_request` handlers moved out of `tools/kanban_tools.py`, which registers `kanban_supervisor_request` and surfaces `supervisor_requests` in the task view.
- `tools/kanban_tools_schemas.py`, `tools/kanban_tools_db_hint.py`: schema and the unlink-refusal hint that names the new tool.
- `hermes_cli/kanban_db_dispatch.py`: `supervisor_requests` field on the tick result and the reclaim-phase drain.
- `hermes_cli/kanban_parser.py`, `hermes_cli/kanban.py`: `supervisor-requests` subcommand and the worker CLI unlink guard.
- `agent/transports/hermes_tools_mcp_server.py`: `EXPOSED_TOOLS`.

## Tests

`tests/hermes_cli/test_kanban_supervisor_requests.py`:
- Reproduction and denial: `test_same_profile_creator_and_assignee_has_no_capable_supervisor`, `test_worker_sibling_unlink_is_still_denied_and_names_the_route` (tool), `test_worker_cli_sibling_unlink_is_denied_too` (shell CLI).
- Origin and scope: `test_non_worker_origin_is_refused`, `test_stale_run_is_refused`, `test_delegated_child_is_refused`, `test_foreign_archived_link_and_cycle_ops_are_refused`, `test_a_batch_with_any_link_op_is_refused_up_front`.
- Preflight and dedupe: `test_dry_run_preflight_separates_tool_names_from_effective_scope`, `test_absent_executor_leaves_a_visible_agent_owned_pending_request`, `test_duplicate_requests_return_the_same_row`.
- Execution and receipts: `test_executor_applies_and_correlates_receipts_and_wakes_without_releasing`, `test_rejected_and_failed_requests_report_without_releasing`, `test_archived_requester_is_rejected_at_execution`, `test_result_before_park_still_wakes_the_requester_once`, `test_a_late_result_does_not_wake_an_unrelated_park`.
- Leases and crashes: `test_busy_executor_keeps_the_request_queued_then_lease_expiry_redelivers_once`, `test_crash_after_accept_is_redelivered_by_the_next_executor` and `test_a_request_that_keeps_crashing_executors_is_failed_not_redelivered_forever` (real child processes).
- Executor context: `test_executor_refuses_inside_a_worker_and_the_tick_skips`, `test_dispatcher_tick_is_the_executor` (dispatcher tick), `test_dispatcher_drain_honours_the_kill_switch`, `test_a_leaked_worker_env_in_the_dispatcher_is_logged`.
- End to end: `test_end_to_end_worker_request_to_separate_non_worker_executor_process` (request via tool, drained by `hermes kanban supervisor-requests --drain --json` in a separate process).
- `test_codex_runtime_workers_can_reach_the_tool` (MCP exposure).
- The `--task`, `--status` and list-only forms of the operator CLI are untested. The 1280/1281 wording changes carry no test.

## Applying

Patches are not independent of the rest of the mirror: apply the series in order (`git am -3`) onto the fork base. 1279-1281 touch the files 1278 creates.

## Gotchas

- Only `unlink` ops are filed. Add new edges yourself with `kanban_link` first, then request the unlinks.
- A request is not a release: the requester must park (`kanban_schedule` / `kanban_block kind='time'`) naming the request id to be woken, and still has to complete itself.
- With no executor seen in 5 minutes the request stays `pending` and visible (`executor_available: false`); drain it with the operator CLI.
- Not claimed: live deployment of the dispatcher on any host.
