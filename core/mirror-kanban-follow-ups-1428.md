# Kanban runtime follow-ups in the 1428–1468 mirror

This document covers patches **1433–1437, 1446–1448, 1453–1454, 1456–1457, 1460, 1464–1466**. It describes their combined behavior, including later corrections, from the actual patch hunks. Patch 1468's quota-test adjustment is documented separately by the CI lane.

## Behavior

- **Continuation recovery (1434–1437, 1446):** a worker can submit `kanban_supervisor_request` with `ops=[{"op":"resume","task_id":"<target>"}]`. A non-worker executor releases only a current, kernel-evidenced `continuation_exhausted` park. Parent gating remains: the result can be `todo`, not necessarily `ready`. The dispatcher creates a fresh, in-scope recovery requester for eligible parks; creating that card does not itself resume the target.
- **Assignment versus execution authority (1434–1435):** an assignment repair cannot grant permission to unblock another card or reset its continuation budget. Such asks return `execution_authority_mismatch`; ordinary profile mismatches and historical descriptions of previous transitions remain eligible for assignment repair.
- **Retry breakers (1447–1448):** `kanban_create(max_retries=5)` sets a per-card breaker; accepted explicit values are integers 1–10, excluding booleans. Omission leaves the database field null for the configured/default policy. An existing direct child can be changed through `ops=[{"op":"retry_limit","task_id":"<child>","max_retries":5}]`, not by editing the requester's own breaker.
- **Target-policy inspection (1453, 1457):** capability admission, explicit assignee model/provider resolution and explicit-profile PR command-prefix lookup inspect the target profile's policy rather than inheriting the creating worker's frozen configuration. Inspection does not replace that worker's execution authority.
- **Reviewed PR continuation (1454, source `e5e1b9972b43869ceecb9488c0668744342e2713`):** already-finished, unchanged historical prerequisites do not require another review when fresh awaited reviewers authorize continuation. Merged, closed or rejected PRs can be discharged while evaluating the remaining reviewed PRs. Permission is consumed by the next claim, not reusable after a crash/reclaim.
- **Adopted-card notices (1456):** remote-notice indexing and sweeping include local origin chats named in `origin_bindings`, not only unreleased shepherd session links; a chat present in both is deduplicated. The shared selector serves done and blocked paths. The new adopted-origin regression cases exercise **done** notices; adopted **blocked** delivery is not directly exercised by these added cases.
- **Direct triage parking (1464–1465):** schedule and time-block paths can move `triage` directly to `scheduled`, without an intermediate claimable `ready` state. A DB-level evidenced human block can also park triage in `blocked`. These transitions clear the dispatcher failure streak/error but retain block-loop recurrence evidence. The default workflow advertises the added scheduled/blocked moves.
- **Sync repair cost (1433, 1460, 1466):** materialized row metadata replaces repeated schema queries; placeholder retirement loads placement configuration once per pass; tombstone reads select only the relevant table families. These are bounded-work changes, not claims of measured production latency improvement.

## How it works

### Resume and authority routing

`kanban_supervisor_requests` normalizes `resume` as its own operation family, dispatches preflight to `kanban_resume_exec`, and executes through `kanban_db.unblock_task`. A request cannot mix resume with edge-edit or notification-reconciliation operations.

Eligibility checks include all of the following:

- The target is not the requester itself. It is either directly created by the requester, or an ancestor within three creator hops, using kernel-stamped provenance.
- For an ancestor target, the requester's `created` event must follow the target's current park event. Directly created children do not need this freshness exception check (1436 pins that distinction).
- The target is parked in `triage`/`blocked`, has no current run and no human gate. The newest relevant lifecycle event must be a block rekinded `continuation_exhausted`, with an earlier kernel exhaustion refusal on that same run.
- At most two supervisor resumes are allowed over the target's lifetime. A finished requester, a released target or a later unrelated park fails closed.

Patch 1435 adds `unblock_task(precondition=...)`: requester and target checks run again inside the unblock write transaction, before modifying the task. A human gate arriving after preflight is therefore retained. The unblock event carries `via=supervisor_resume`, request ID and requester. Redelivery finds that event and reports `already_applied`; results include status/current run/unsatisfied parents. Patch 1437 narrows post-commit readback handling to SQLite, attribute, type and value errors, reporting `readback_error` rather than hiding a committed write. It also compacts the DB facade without changing its transition logic.

Patch 1446 adds the missing producer: the parked-card sweep creates a scratch recovery card with `created_by=kanban-recovery`, `creator_task_id=<target>` and key `recovery:<target>:<park event id>`. It inherits the target's assignee (or default) and tenant. The target records `resume_requester_created`. Human gates, non-exhaustion parks, spent resume budgets and recovery cards themselves do not generate a requester. The stale-triage sweep waits while this park's recovery card remains open.

The assignment classifier matches imperative transition/reset asks, not a blanket mention of authority vocabulary. Completion validation also checks the blocked-event reason behind legacy assignment repairs: merely proving a profile exists cannot complete a repair for an authority gap. Its error points to archiving the obsolete repair and using the supervisor route; that is guidance, not an automatic archive.

### Retry limits

Create argument validation happens before placement and passes `max_retries` to `kb.create_task`. The remote creation transport represented by these patches cannot carry that option: automatic placement stays local, and an incompatible explicit remote placement is refused with `placement_unavailable` rather than dropping the breaker.

`retry_limit` is another standalone request family. It accepts only a direct child created by the requester, never self, grandchildren, unrelated cards or done/archived targets. The executor rechecks requester/target eligibility, calls `kanban_db_operator.edit_operator_fields`, reads the value back, and appends `supervisor_retry_limit` provenance. Results report old/new/readback; an already-equal value is a preflight no-op. Redelivery recognizes either its audit mark or the requested value already holding and avoids a second edit. This is not a claim that its checks have the resume path's in-transaction precondition protection.

### Profile and PR evidence

`load_profile_config_effective(path, fail_closed=..., record_backup=False)` is the explicit-target projection. Session/runtime readers retain `load_user_config_effective` and its bound snapshot. Callers bind and restore the target home/secret scopes. The capability reader uses the new projection; model assignment, configured-provider and PR-prefix readers use it only for an explicit target. No-target paths retain caller authority. Managed overlays and target secret expansion still apply; target inspection does not create configuration backups by default.

Review checkpoints now record `awaited` parents in addition to all captured parents and PR URLs. Legacy checkpoints infer historical parents only from a terminal current status, a last run ended strictly before the park, and no later substantive lifecycle events. Comments, archival and late `worker_result` receipts do not reopen finished work; subsequent lifecycle work invalidates the exemption. Missing nonlocal parents remain awaited.

A present `pr` reference must resolve uniquely within the checkpoint: a positive integer, `#number`, `owner/repo#number` or captured URL. Present-but-invalid/ambiguous values do not get the missing-field fallback. The PR guard subtracts discharged URLs before asking whether fresh review evidence covers the remaining URLs. Rejection comments preserve the rejection and instruct the worker to follow superseding scope rulings, not blindly resubmit or create a duplicate.

### Notices, parking and sync

`_linked_chats` unions this gateway's unreleased shepherd links with origin bindings and deduplicates `(profile, session)` pairs. Both `_index`/poller collection and `_sweep_cards` use that selector; foreign origins are excluded.

Scheduling, timed parking and dependency-block SQL include `triage` directly and reset failure state conditionally on the old status. Existing expected-run, human-answer, awaiting-producer and wake-check guards remain. Workflow metadata adds scheduled/blocked to triage's manual destinations; no new Desktop interaction test is supplied with that metadata-only follow-up.

`_row_fields` filters `row.keys()`/values through the synced-column allowlist, excluding local/key/server columns, while preserving blob encoding and run-ID-to-UID lookup. Placeholder classification takes an optional per-pass config snapshot, refreshed on the next retirement pass. Missing-child repair restricts tombstones on both live and shadow to `tasks`, `task_links`, `task_chat_links`; placeholder retirement restricts live tombstones to `task_chat_links`. Relevant deletion and pending/rejected outbox vetoes remain.

## Code

Source paths are in the Hermes core repository, not standalone files in this documentation repository.

| Patches | Source files |
|---|---|
| 1433, 1460, 1466 | `hermes_cli/kanban_sync_client.py`; 1460 also `hermes_cli/kanban_placement.py` |
| 1434–1437 | `hermes_cli/kanban_resume_exec.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_assignment_routing.py`, `hermes_cli/kanban_supervisor_requests.py`, `tools/kanban_tools_schemas.py` (1436 is test-only) |
| 1446 | `hermes_cli/kanban_resume_requester.py`, `hermes_cli/kanban_block_policy.py`, `hermes_cli/kanban_triage_sweep.py` |
| 1447–1448 | `hermes_cli/kanban_retry_limit_exec.py`, `hermes_cli/kanban_supervisor_requests.py`, `hermes_cli/kanban_placement.py`, `tools/kanban_tools.py`, `tools/kanban_tools_create_args.py`, `tools/kanban_tools_schemas.py` |
| 1453 | `hermes_cli/config_effective.py`, `hermes_cli/kanban_capabilities.py` |
| 1454 | `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_db_dispatch_pr_rejection.py` |
| 1456 | `tui_gateway/kanban_remote_notices.py` |
| 1457 | `hermes_cli/kanban_model_assignment.py`, `hermes_cli/kanban_pin_provider.py`, `hermes_cli/kanban_pr_acceptance.py` |
| 1464–1465 | `hermes_cli/kanban_db_schedule.py`, `hermes_cli/kanban_dependency_review.py`, `hermes_cli/kanban_timed_park.py`, `tools/kanban_tools.py`, `hermes_cli/kanban_workflow.py` |

## Tests

**Source tests were inspected in the patches, not rerun for this documentation task.** The following lists assertions present in those source tests, not fresh execution results. No live-board, deployed-gateway or Desktop end-to-end verification is claimed.

| Patches | Exact source test file | Coverage in the patch |
|---|---|---|
| 1433 | `tests/hermes_cli/test_kanban_sync_row_fields.py` | No SQL for 100 already-materialized non-reference rows; local/unknown columns excluded, blobs encoded, newly added synced columns visible without a global schema cache; task/event run references resolve, including missing and null values. |
| 1434–1435 | `tests/hermes_cli/test_kanban_assignment_routing.py` | Authority asks refused without assignment repair/mutation; genuine profile mismatches retained; imperative forms distinguished from history; own-card IDs excluded; legacy authority repair cannot complete on profile existence; registered block-tool error is typed. |
| 1434–1436 | `tests/hermes_cli/test_kanban_supervisor_resume.py` | Tool-handler dry-run/submission through dispatcher tick and a separate CLI drain process; actual subsequent claim; parent gating and unchanged edges; creator-chain acceptance/rejection; direct-child freshness exemption; current-park freshness; human gates before and during execution; stale requester run, released/live/reparked target, archived requester, duplicate requests, mixed/extra arguments and resume cap; process death after apply with one unblock on redelivery; worker drain refusal and registered schema. |
| 1437 | No test file changed | Existing resume tests above are related coverage; no dedicated injected-readback-error regression is added by this cleanup. |
| 1446 | `tests/hermes_cli/test_kanban_resume_requester.py` | Dispatcher tick creates one fresh requester per park; tool submission and executor application; later park gets a new requester and old one fails freshness; human/non-exhaustion/foreign target refusals; spent budget and recursive recovery suppression; stale-triage sweep waits for the open requester then proceeds after completion. |
| 1447 | `tests/tools/test_kanban_tools.py` | Create handler persists explicit breaker; omission remains null; invalid range/type creates no task; schema exposes integer field. |
| 1447 | `tests/hermes_cli/test_kanban_placement.py` | Uncarried option classification, local create retaining breaker, explicit incompatible remote pin refused without local or remote creation. |
| 1448 | `tests/hermes_cli/test_kanban_retry_limit_exec.py` | Tool-handler preview/submission and non-worker drain; old/new/readback and edited event; self/foreign/grandchild/unknown/terminal target refusal; invalid types/range/extra fields, equal-value no-op, mixed families and schema; target archived or requester completed before execution; redelivery after suppressed receipt writes only once; worker drain refused. Despite its name, `test_lowering_an_existing_breaker_reports_old_value` asserts a change from 3 to 7, not lowering. |
| 1453 | `tests/hermes_cli/test_kanban_worker_capabilities.py` | Subprocess frozen-worker admission across caller/target toolset combinations for default and named target profiles; correct capability rejection without row creation; original snapshot/runtime reads preserved and no backup creation. |
| 1453 | `tests/hermes_cli/test_kanban_capabilities.py` | Existing unreadable-policy fail-closed test's monkeypatch is redirected to the new target reader. |
| 1454 | `tests/hermes_cli/test_kanban_dependency_review_history.py` | New/legacy mixed historical checkpoints with merged/closed/rejected old PRs; one continuation only, including after reclaim; rejection comment content/deduplication; uncovered open/unknown PRs remain held; unique reference forms, invalid/ambiguous references, historical lifecycle invalidation versus harmless bookkeeping, missing remote parent, malformed/empty awaited evidence. |
| 1456 | `tests/tui_gateway/test_kanban_remote_notices.py` | Adopted-origin done notice via transcript-writing sweep and poller collect; origin+shepherd deduplication and foreign-origin exclusion. Added fixtures are done-only, not adopted blocked-notice cases. |
| 1457 | `tests/hermes_cli/test_kanban_worker_target_policy.py` | Subprocess model/provider/PR readers with valid and malformed explicit target policy; no-target caller fallback; home, secret scope and frozen authority restoration; no target backups. |
| 1457 | `tests/hermes_cli/test_kanban_worker_target_scope.py` | Target `.env` expansion and managed-overlay variants across model/provider/PR readers; credential callback sees target scope; caller scope/snapshot restored afterward. |
| 1460 | `tests/hermes_cli/test_kanban_placeholder_batch_cost.py` | One config read per 1,000-link retirement pass and refresh on next pass; snapshot classifier parity for default/custom/string/map/empty/session-reference shapes. |
| 1460 | `tests/hermes_cli/test_kanban_sync_placeholder_links.py` | Changed custom-placeholder config retires the shadow-only placeholder on settlement while a lost real-chat link remains a reported mismatch. |
| 1464 | `tests/hermes_cli/test_kanban_triage_park.py` | Schedule/time-block through DB, CLI parser/command and tool handler; SQL trigger records only scheduled, no intermediate state; failure streak reset, recurrence and machine pin preserved; dispatcher does not spawn before wake; due wake releases; DB human block; terminal/review/stale-run refusals and producer/probe guards with no changes. |
| 1465 | No test file changed | Workflow destination tuple only; the 1464 tests cover underlying transitions, not a Desktop/manual-move interaction. |
| 1466 | `tests/hermes_cli/test_kanban_sync_repair_history_cost.py` | Under ten 1,000-step SQLite progress callbacks with 10,000 irrelevant event tombstones, on live/shadow and repair/retirement variants; relevant task/link/chat tombstones still veto repair; pending/rejected and deleted chat links still veto retirement. |

## Applying

Use the complete ordered mirror and the snapshot-specific replay plan/policy described in [README.md](README.md), including its upstream bridge. These patches depend on existing Kanban schemas, lifecycle APIs, supervisor-request infrastructure, frozen worker policy and remote-notice machinery. They are not a standalone install or a proven independent cherry-pick set.

Keep the sequence intact: 1435–1437 correct/refine 1434; 1446 supplies its automatic fresh requester; 1448 shares the retry cap with 1447; 1457 depends on the explicit-target reader added by 1453; 1464 updates dependency-review code already changed by 1454, and 1465 aligns workflow metadata. Later sync changes build on the same client edited by 1433.

This documentation task did not replay the series or run the source suites. Consult the enclosing mirror verification report for any executed inventory/replay/tree checks. Running these tests later requires an isolated board/database and the source repository's supported test environment; do not aim them at a live board or clear inherited worker security fences to make them run.

## Gotchas

- A pending supervisor request is not an applied result. Read its receipt and target state; a successful resume can remain parent-gated in `todo`.
- Neither a profile change nor prose saying a human decision was resolved confers lifecycle authority. Resume does not waive human gates, change acceptance or remove dependency edges.
- Resume's lifetime cap and consecutive-continuation policy are different controls; the latter is not raised by this operation. A fresh recovery card is keyed to the current park, not reusable indefinitely.
- `retry_limit` only changes the breaker. It does not unblock a parked child, reset failures or grant self-edit authority. Keep operation families in separate requests.
- Remote placement with an uncarried breaker is refused rather than silently losing the value. Local fallback is the automatic-placement behavior documented here, not permission to ignore an explicit remote pin.
- Explicit-target configuration inspection is not a replacement for session/runtime snapshot reads. Malformed target policy must not be replaced with the caller's permissive policy.
- A missing PR reference and an invalid present reference are different. Numeric references ambiguous across captured repositories authorize neither.
- Direct triage parking retains recurrence evidence and existing park guards. Workflow metadata alone does not prove a Desktop interaction works.
- Per-pass config snapshots refresh next pass; they are deliberately not process-global caches. Tombstone filtering must retain task-level settlement vetoes as well as child-link deletion vetoes.

## Plugin?

**Core patches, not a plugin.** They modify transaction-time lifecycle guards, dispatcher recovery production, request normalization/execution, tool contracts, frozen-policy readers, sync internals and gateway notice selection. A plugin may consume the public tools or request receipts, but cannot replace these internal guarantees merely by registering a hook.
