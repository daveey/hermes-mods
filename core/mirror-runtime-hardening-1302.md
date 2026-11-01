# Runtime hardening series, ordinals 1302-1326 (subset)

Covers ordinals **1302, 1305, 1310, 1311, 1312, 1313, 1314, 1315, 1316, 1318, 1319, 1324, 1325, 1326, 1539** of the numbered fork series. The ordinals in between belong to other docs. Patch files are `patches/<ordinal>-<format-patch slug>.patch`; the fork commit short hash is given per section.

Every test listed below is a test file or test case **read from the source diff only**. None were executed in this mirror pass. Nothing here was checked against a live Kubernetes cluster: the Kubernetes backend tests use an in-process fake cluster and mocked `kubernetes` client objects (see 1316).

## Applying (whole series)

Apply the numbered series in order on the fork merge-base (`git am -3`), not as standalone patches. These changes share files and assume neighbouring ordinals:

- 1313 -> 1314 -> 1324 touch `hermes_cli/kanban_owner_archive_relay.py`; 1314 -> 1324 also touch `hermes_cli/kanban_placement_forward.py` and `tests/hermes_cli/test_kanban_placement.py`.
- 1325 -> 1326 both edit `agent/runtime_session_store.py` (1326 reuses `_receipt_timeout_s()` from 1325) and `tests/agent/test_worker_rpc_connection_reuse.py`.
- 1305 calls `tui_gateway.notice_fanout.mark_stale_blocked_notices` / `_BLOCKED_LINE`, which come from earlier work in the series.
- 1302 uses `agent.core_update_drain.check_drain_hold`, `cron.planned_pause.begin` and `gateway.run_shutdown_restart_wait`, all pre-existing in the series.

None of these is a plugin: all are core changes.

---

## 1. Cron and core-update drain (1302)

Patch `1302-fix-cron-defer-due-jobs-under-the-core-update-drain-.patch` (fork `ead069dd57`).

### Behavior
- While a core-update drain hold stands (`<home>/core-update/drain-hold`, the same signal the Kanban dispatcher reads), the cron tick starts nothing and returns 0. Due jobs stay due and are not advanced.
- Each due job is named once per hold in an INFO line ("deferred by the core-update drain hold; it runs once after the hold"). A release line lists the deferred ids when dispatch resumes.
- A recurring job that missed several slots runs once after the hold. A one-shot is covered by the existing planned pause, so it is not retired as missed.
- A hold whose writer process died does not count. A read error fails open (cron keeps running).
- Restart wait: an update intent file containing `"cron_cut": true` puts awaitable cron runs under the same 15 s core-update cap that Kanban-only waits already had. Each cut run is logged with job id and start time. A chat turn still gets its full wait; a plain intent still waits for cron.

### How it works
`cron/scheduler_tick.py::tick` calls a new `drain_held()` after retirement admission. It uses `check_drain_hold("cron", logger)`; on hold it calls `planned_pause.begin()` (as the update-admission branch does) and returns 0. `_log_drain_deferred_jobs()` is read-only: it lists enabled, non-paused jobs whose `next_run_at` is past, tracked in a module set cleared on release.

`gateway/run_shutdown_restart_wait.py` gains `_intent_cuts_cron(intent)` (re-reads the tiny JSON file each poll; unreadable or missing means no cut), adds the awaitable cron count to the Kanban-only count when the intent cuts cron, logs `_log_cut_cron_runs()` (skipping `restart_safe` scoped workers) when the cap expires, and applies the same count in the stop-leash decision. The writer of `cron_cut` is the external update tool, which is not part of this patch.

### Code
`cron/scheduler_tick.py`, `gateway/run_shutdown_restart_wait.py`.

### Tests
Source tests inspected, not executed here:
- `tests/cron/test_drain_hold_defers_tick.py`: a job due during the hold does not start, `next_run_at` is unchanged after three ticks, and exactly one naming line carries job id and name; a job 40 minutes overdue runs exactly once on the first tick after the hold and is re-armed in the future; a dead drainer pid does not freeze cron; a one-shot due inside the hold fires after the hold.
- `tests/gateway/test_core_update_cron_cut.py`: with `cron_cut` the wait is 15 s (with 0 or 2 Kanban cards) and two "cutting cron run" lines appear with job id, "started" and "next tick reruns it"; a plain intent waits 1800 s; a chat turn keeps 1800 s even under `cron_cut`; unreadable/missing intent does not cut; the stop leash covers cut runs only when `cron_cut` is set.

### Applying
`git am -3` 1302 in series order. Self-contained in the files it touches; the hermes-sync side that writes `cron_cut` is external and not in this patch.

### Gotchas
- The hold file is trusted through `check_drain_hold`; a stale file with a live unrelated pid would defer cron.
- Cut cron runs are killed by the restart and rerun on the next tick, so jobs must be restart-safe.
- No hold is read for one-shots beyond the planned pause; a very short grace could still retire one (the test shortens grace to 1 s to prove the pause covers it).

### Plugin?
No.

---

## 2. Superseded blocked notices (1305)

Patch `1305-fix-notices-a-blocked-notice-whose-card-moved-on-nev.patch` (fork `e555afdb33`).

### Behavior
A "blocked" Kanban notice whose cards are no longer `blocked` or `triage` (answered and unblocked, rescheduled, done, archived), or a remote or board-gone notice the fan-out marked `stale`, no longer wakes a "[Kanban close-out]" turn. The notice is stamped `closeout.state = "superseded"` and only the chat outcome is refreshed. A notice whose card is still blocked by an agent still dispatches a close-out.

### How it works
`tui_gateway/notice_guarantee.py` adds `superseded(cand)`: extract card ids with `_BLOCKED_LINE`; for remote notices (or no ids) return the `stale` flag; otherwise connect to the board named in metadata or event keys, load every card, return the `stale` flag if the board or any card is missing, else true when every card is outside blocked/triage. `_supersede` writes `display_metadata` with a compare-and-swap on the row (returns `skipped:claimed` if another backend won). `_handle` checks `superseded` after the held/live-turn checks and before the blocked-on-human branch.

### Code
`tui_gateway/notice_guarantee.py`.

### Tests
Source tests inspected, not executed here, in `tests/tui_gateway/test_notice_guarantee.py`:
- `test_a_blocked_notice_whose_card_moved_on_is_superseded_not_woken[unblock|archive]`: no wake call, result `superseded`, closeout state `superseded`, second pass does nothing.
- `test_a_remote_blocked_notice_marked_stale_is_superseded`.
- `test_a_blocked_notice_whose_card_is_still_blocked_by_an_agent_still_gets_a_closeout` (control: result `dispatched`).

### Applying
`git am -3` 1305 after the earlier notice fan-out work it calls (`mark_stale_blocked_notices`, `_BLOCKED_LINE`).

### Gotchas
Judging uses the live board at handle time; a notice for a card that flips back to blocked later is not resurrected. The commit message says 3 tests failed before the change; that was not reproduced here.

### Plugin?
No.

---

## 3. Freshness test fixture (1310)

Patch `1310-test-web-avoid-repeated-library-checks-in-freshness-.patch` (fork `20c094bf67`).

### Behavior
Test-only. The built-web freshness JS test builds its temporary web project with `skipLibCheck: true` in `tsconfig.app.json` so the six real builds do not re-check TypeScript's bundled `.d.ts` files each time.

### How it works
`webSource(source, compilerOptions = {})` spreads extra compiler options into the generated tsconfig; the freshness test passes `{ skipLibCheck: true }`.

### Code
`tests-js/product-builders.test.mjs`.

### Tests
The existing `built web freshness follows shared sources and build inputs, not mtimes or ...` test is the one affected. Source inspected, not executed here; no product path changes so none is claimed.

### Applying
`git am -3` 1310. Test file only; no ordering dependency.

### Gotchas
Only the freshness test passes the option; other callers of `webSource` keep a full lib check.

### Plugin?
No.

---

## 4. Frozen legacy alias no longer restores links (1311)

Patch `1311-fix-kanban-a-frozen-legacy-alias-no-longer-restores-.patch` (fork `7af0b82411`).

### Behavior
Removing a shepherd subscription on the owner now sticks. The pre-qualification (`board/id`) copy of a card stays in the hub replica forever; the fold used to copy its shepherd links back onto the qualified twin (109 on one card), tying a chat to a card it had only read. Now only a twin that is `done` or `archived` takes the alias's shepherd links (for the terminal release); an open twin keeps only its own links.

### How it works
In `_fold_legacy_aliases` the link list starts as the twin's own sessions, and alias shepherd links are appended only when `twin.status in ('done', 'archived')`. Matching rules (single profile on the board/id, equal non-null `created_at` and `created_by`) are unchanged.

### Code
`hermes_cli/kanban_session_activity_records.py`.

### Tests
Source test inspected, not executed here: `tests/hermes_cli/test_kanban_remote_terminal_release.py::test_a_link_the_owner_released_does_not_come_back_from_a_frozen_alias` (a legacy record with an extra `s_old` link and a `scheduled` twin yields only the twin's session). Neighbouring tests for fold-into-twin and terminal release exist in the same file and were not changed.

### Applying
`git am -3` 1311. Independent of the other ordinals here.

### Gotchas
A card that finishes after an owner released a link will receive the stale alias link at terminal time, which is intended for terminal release but could notify a chat that had unsubscribed.

### Plugin?
No.

---

## 5. External gateway drain holds dispatcher claims (1312)

Patch `1312-fix-kanban-an-external-gateway-drain-holds-dispatche.patch` (fork `ddc3f813f3`).

### Behavior
An external drain (`.drain_request.json`) now holds Kanban dispatcher claims exactly like a deferred restart: ticks run with `suppress_spawn`, `bad_ticks` stays 0, and the "dispatcher stuck" warning is not emitted. One INFO line at start names the principal and since-time; one at release says claims resume. Running workers continue. Before, the dispatcher gated only on `_restart_requested`, kept claiming into a gateway that refused every new worker, and blamed profile health after 48 ticks.

### How it works
In `gateway/kanban_watchers.py` the loop reads `self._external_drain_active`; when the existing gate is None it becomes `"drain"`. It stores `(principal, since)` the first time, via `_external_drain_hold` run in a thread, and logs the release when the flag clears. `stuck_warning` is now guarded by `gate is None`. `_external_drain_hold` in `gateway/kanban_watchers_dispatcher.py` reads the marker with `gateway.drain_control.read_drain_request()` (never raises) and formats `requested_at` in local time, falling back to the observation time and `"unknown principal"`. The gate is the runner flag, so an unreadable marker only degrades the log.

### Code
`gateway/kanban_watchers.py`, `gateway/kanban_watchers_dispatcher.py`.

### Tests
Source tests inspected, not executed here, in `tests/gateway/test_kanban_external_drain_hold.py`:
- `test_external_drain_suppresses_spawns_and_never_reports_stuck`: every tick has `suppress_spawn=True`, no stuck line, exactly one hold line `... (stale-plugin-restart, since HH:MM:SS); running workers continue`.
- `test_external_drain_claims_nothing_on_a_real_board`: the ready card stays `ready`, nothing spawned.
- `test_drain_release_spawns_on_the_next_tick_and_logs_the_release`.
- `test_external_drain_without_a_readable_marker_still_holds` (`unknown principal`).
- `test_no_drain_control_ticks_spawn_and_still_report_stuck` (control).
- `test_deferred_restart_drain_is_unchanged_and_never_reports_stuck`.

### Applying
`git am -3` 1312. Independent of the other ordinals here; needs the existing `gateway.drain_control` module.

### Gotchas
The stuck warning is now suppressed on any gated tick, including estop, by design. The since time is local clock without a date.

### Plugin?
No.

---

## 6. Creator may archive its own done card (1313)

Patch `1313-fix-kanban-a-worker-may-archive-a-done-card-its-own-.patch` (fork `bb08052cd8`).

### Behavior
A scoped Kanban worker may now archive a **done** card its own task created, provided the card has no live claim and no open card lists it as a parent. Scratch waiters completed by their own worker used to leak (six on one day). Still refused: running or claimed cards, done cards with open children (error names the children), cards another task created, an archived card, and the worker's own assigned task. The tool schema text and `kanban_archive` docstring are updated.

### How it works
The archive-scope rules move from `hermes_cli/kanban.py` into new `hermes_cli/kanban_archive_scope.py` (names re-exported from `hermes_cli.kanban` to keep that module under its line cap). `_worker_may_archive_own(conn, task)` now takes the connection, checks `archived`, claimed/running, and for `done` queries `task_links` joined to `tasks` for children whose status is not done/archived. Capability-owner and stranded-`runs_on` rules are moved unchanged; refusal wording becomes "unclaimed or done card".

### Code
`hermes_cli/kanban_archive_scope.py` (new), `hermes_cli/kanban.py`, `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`.

### Tests
Source tests inspected, not executed here, in `tests/hermes_cli/test_kanban_creator_archive_done.py`: tool path archives its own done card; CLI path `_cmd_archive` archives it; own running card refused (message mentions claim); done card with an open child refused and the child id is in the error; allowed once the child is done; foreign done card refused; own assigned task refused ("assigned task").

### Applying
`git am -3` 1313 before 1314 and 1324 (they build on the relocated `worker_archive_scope` and the relay module).

### Gotchas
Both entry paths (tool handler and CLI) are tested; the relayed-owner path (`archive_scope_for` from `kanban_owner_archive_relay`) shares the code but has no new test here. Any importer of the moved private helpers must use the re-exports.

### Plugin?
No.

---

## 7. Archiving a placement-forwarded card archives its remote copy (1314) and the deferred sweep (1324)

Patches `1314-fix-kanban-archiving-a-placement-forwarded-card-arch.patch` (fork `b357101aee`) and `1324-fix-kanban-archive-a-placed-copy-whose-remote-id-res.patch` (fork `96698fb109`).

### Behavior
- 1314: archiving a card that placement forwarded to another gateway also sends an archive request for the remote copy (`request <gateway> tasks default/default/<remote id> archive`). `kanban_archive` output gains `placed_copy: {gateway, task_id, status}` with status `archived`, `pending`, `unresolved` or `failed` (plus `error`/`note`). One request per card; a failed forward is sent again on the next call. A target that is unreachable queues the request durably (hub client return code 3).
- 1324: when the target has not yet made the card (request sent, no remote id), the archive first waits up to 5 s for the id, then records `unresolved`. A dispatcher-tick sweep re-reads the hub replica and sends the archive once the id arrives (event goes `unresolved` -> `pending`). After 7 days still unresolved, an `expired` event and a comment are written and the card is not swept again.

### How it works
`archive_task` moves to `hermes_cli/kanban_db_archive.py` (the old body in `kanban_db.py` becomes a delegating wrapper) and ends by calling `forward_archive(conn, task_id)`. `hermes_cli/kanban_placement_forward.py` adds the `placement_archive_forwarded` event kind, `_archive_events`, `archive_forward_view` (turns a `pending` request into `archived` when `kanban_placement.remote_state` reports done), and `forward_archive`, which never raises (failures go to stderr). It resolves the remote id with `_resolve_remote_id` (recorded id, replica, then `kanban_placement.await_task_id` within `ARCHIVE_AWAIT_BUDGET` = 5 s) and records `expired` as terminal along with `pending`/`archived`. `sweep_unresolved_archives` calls `forward_archive(..., await_budget=0, record_unresolved=False)` for archived cards whose last event is `unresolved`. `worker_archive` in `kanban_owner_archive_relay.py` appends `placed_copy` to a worker-forwarded archive result, and `apply_remote_archives` runs the sweep after the existing sources (a failing sweep is logged and skipped).

### Code
`hermes_cli/kanban_db_archive.py` (new), `hermes_cli/kanban_db.py`, `hermes_cli/kanban_placement_forward.py`, `hermes_cli/kanban_owner_archive_relay.py`.

### Tests
Source tests inspected, not executed here, in `tests/hermes_cli/test_kanban_placement.py`:
- 1314: `test_archiving_a_forwarded_card_archives_the_remote_copy` (argv shape, `placed_copy` pending, event list `["pending"]`, flips to `archived` once the hub row is done, second archive sends nothing); `test_archive_forward_is_queued_when_the_target_is_offline` (rc 3 queued); `test_archive_forward_failure_is_reported_and_retried_and_plain_cards_are_untouched` (rc 1 gives `failed` with error, retry gives `pending`, a plain card has no `placed_copy` and sends nothing).
- 1324: `test_archive_waits_briefly_for_the_remote_id_and_sends_the_archive`; `test_unresolved_archive_is_sent_by_the_dispatcher_sweep_once_the_target_makes_the_card` (events `["unresolved","pending"]`, exactly one request, later ticks send nothing); `test_unresolved_archive_sweep_gives_up_after_seven_days_with_a_comment` (nothing at 6 days; `expired` plus a "gave up" comment past 7 days; not swept again).

### Applying

Apply the complete numbered series through 1324 with `git am -3 --empty=keep` at the recorded fork merge-base. 1314 adds placement forwarding and 1324 adds the deferred sweep; neither is a standalone patch.

### Gotchas
- The hub CLI path and request shape come from the capability registry and placement config; a host without them records `failed`.
- Forwarding happens after local commit, so a local archive never fails because of it.
- The 5 s wait runs in the archive call path and blocks it that long when the target is slow.
- 1324 depends on the 1314 event kind and view.
- Hub behaviour is mocked by a fixture (`mac` fixture with a fake `hermes-hub` runner and a hub sqlite); no real hub was used.

### Plugin?
No.

---

## 8. Session outcomes: lost requests, crons, refused-turn replay (1315; follow-up 1539)

Patch `1315-fix-sessions-a-lost-request-outranks-live-work-a-cro.patch` (fork `2185d7299c`).

### Behavior
- A person's typed or steered message that failed or got no answer is now `unresolved` (reason `turn_failed` or `interrupted`) even when the chat owns open cards or crons, because a chat painted "waiting" hid it from the only person who can resend. A real reply, or a held outcome declaration, closes it; an automation-interrupted notice does not count. An open decision still outranks it.
- A recurring cron no longer counts as a wait. A finished chat is `done` and carries `crons` naming each job. The Desktop stripe keeps the loop glyph in the green of done and the tooltip adds "Keeps running: <names>". Open cards still make a chat `waiting`; a declared wait on a cron is honoured.
- A turn refused for a retryable reason keeps its turn marker and is replayed through auto-continue after 45 s, then 180 s, under the existing guards (feature enabled, `max_attempts` can only lower the count, no active goal, not a bot room, not a process/notice turn). A second refused message joins the first prompt. A skewed backend leaves the marker for the boot sweep. A marker that aged out refreshes the outcome.

### How it works
`hermes_state_outcome.py`: `_lost_request(tail)` scans the tail in order, tracking failed-turn rows and returning `turn_failed`/`interrupted` at the first typed user row; `_derive` calls it before the waits (skipped when the turn declared an outcome); `_cron_items` leaves `_live_work` and is read in `_derive` to add `crons` to `done` outcomes, including declared `done`. In `tui_gateway`, `_hold_refused_turn` (called when the turn receipt commits) records the delay and retry-after and the joined prompt; `_retire_turn_marker` keeps the marker while `_refused_turn_marker_key` is set; `_schedule_refused_turn_retry` starts a daemon `threading.Timer` after `message.complete`; the timer calls `_maybe_schedule_auto_continue` unless `update_pause._detect_skew()` reports skew. The Desktop changes are in `session-outcome.ts` (`outcomeHasCrons`, tooltip), `session-row-stripe.tsx`, the English string `outcomeCrons`, its type, the key list and the overlay-gap entry for eight other locales.

### Code
`hermes_state_outcome.py`, `tui_gateway/prompt_turn.py`, `tui_gateway/session_auto_continue.py`, `apps/desktop/src/lib/session-outcome.ts`, `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`, `apps/desktop/src/types/session-outcome.ts`, `apps/desktop/src/i18n/*` and `locales/_keys.desktop.json`.

### Tests
Source tests inspected, not executed here:
- `tests/hermes_state/test_session_outcome.py` (14 new cases): failed typed request beats an open card; beats a cron and a later refused automation row; no live work gives `turn_failed` without the judge; a real reply after the failure clears it; interrupted automation delivery stays `waiting`; an unanswered typed row gives `interrupted`; a kanban-notice tail stays `waiting`; an open decision still wins; a recurring cron gives `done` with `crons`; declared `done` keeps naming its cron; an open card still waits beside a cron; a declared cron wait is honoured.
- `tests/tui_gateway/test_refused_turn_retry.py`: marker kept and 45 s timer; non-retryable failure clears the marker; replay waits 180 s then gives up; process-complete and kanban-notice turns not replayed; a goal keeps its own retry; a second refused message joins the first; a working new message ends the replay; the timer calls auto-continue; a skewed backend leaves the marker; an aged-out marker refreshes the outcome. `tests/tui_gateway/conftest.py` sets `_REFUSED_RETRY_DELAYS_S` to empty by default so no real timer leaks between tests.
- `apps/desktop/src/lib/session-outcome.test.ts`: done-with-crons stays `done`, `outcomeHasCrons` true, tooltip a tooltip retaining its summary and naming both recurring jobs; a plain done is unchanged.

The stripe component change (glyph choice) has no dedicated test; it is reported as untested.

### Applying
Needs the earlier outcome/turn-marker and `agent.turn_failure_copy` work (`FAILED_TURN_DISPLAY_KIND`, `AUTOMATION_INTERRUPTED_NOTICE`) already in the series, and the helpers `_InlineThread`/`_session` from `tests/tui_gateway/test_auto_continue.py`.

### Gotchas
- Replays are bounded by two delays and the auto-continue freshness window; a long outage still ends on "send it again".
- Joining prompts means two refused messages replay as one prompt separated by a blank line.
- Eight non-English locales fall back to English for the new string (listed as overlay gaps).

### Plugin?
No.

### Follow-up 1539: a settled terminal verdict is not replayed

Patch `1539-fix-agent-preserve-terminal-non-retryable-verdict-fo.patch` (fork `37c70bbcaa1c`).

#### Behavior
The generic classifier initially marks some errors as retryable even though settlement later decides they cannot
recover. Examples are a local `ValueError` or `TypeError` raised while building the request, or a request repair
that has run out of attempts. Settlement then ends the turn on the non-retryable client-error path. That turn's
result now always carries `failure_retryable: False`, so the refused-turn replay described above treats it as
terminal:

- The turn marker is cleared.
- No 45 s timer is armed.
- `_maybe_schedule_auto_continue` returns `None`.
- The `message.complete` `error_surface.retryable` is `false`.

Before this patch the result copied `classified.retryable` (`True`), so a turn that could never succeed was replayed
through auto-continue. An error that settlement treats as retryable is unchanged. The test's `TimeoutError`, settled
with retries exhausted, still keeps the marker and arms the 45 s timer.

#### How it works
`agent/turn_recovery.py::nonretryable_client_error_result` sets `"failure_retryable": False` instead of
`bool(classified.retryable)`. Every caller of that builder has already decided the turn is terminal. At this fork
tip the only caller is the `is_client_error` branch of `agent/turn_api_error.settle_unrecovered_error`, which
covers local validation errors, an exhausted image shrink or reasoning repair, and non-retryable classified
reasons. `failure_reason` still reports the classifier's reason.

#### Code
`agent/turn_recovery.py`.

#### Tests
`tests/tui_gateway/test_refused_turn_retry.py::test_terminal_error_auto_continue_follows_settlement` is
parametrized over `ValueError("Account selection requires a live main chat")` → not retryable,
`TypeError("invalid local request")` → not retryable, and `TimeoutError("request timed out")` → retryable.
**It was read from the diff and not executed in this mirror pass.**

The test uses the real `classify_api_error` and asserts that the initial verdict is `retryable is True` for all
three errors. It runs the real `settle_unrecovered_error` (with `retry_count=1`, `max_retries=1` and a stub agent
from `tests/agent/test_nonretryable_result_carries_verdict`) inside a stubbed agent `run`, and asserts that the
settlement action is `"return"`. That result then goes through the real `server._run_prompt_submit`. The test then
asserts:

- The turn marker is present only when the expected outcome is retryable.
- The timers list is `[45.0]` when retryable and `[]` otherwise.
- `_maybe_schedule_auto_continue(...) is None` in the non-retryable case.
- The final `message.complete` has `status == "error"` and `error == str(error)`, and its
  `error_surface.retryable` matches the expected outcome.

**Untested:** the exhausted image-shrink and reasoning-repair branches. The test covers only the local
validation-error branch (non-retryable) and the max-retries branch (retryable).

#### Applying
Apply in series order after 1315 and the earlier work that added `failure_retryable` to the non-retryable result
(the test imports `_Agent` from `tests/agent/test_nonretryable_result_carries_verdict.py`). This patch is separate
from the automation-turn replay (1515), which is documented elsewhere. No config is needed.

#### Gotchas
- **`failure_reason` can still say retryable.** It keeps the classifier's reason, so a consumer that infers
  retryability from the reason alone can still disagree with the verdict. Read `failure_retryable`.
- **Max-retries exhaustion is a separate path.** `max_retries_exhausted_result` keeps its own verdict. This patch
  only changes the non-retryable client-error path.

#### Plugin?
No.

---

## 9. Kubernetes terminal backend (1316)

Patch `1316-feat-terminal-kubernetes-backend-pod-per-session-exi.patch` (fork `a969258dc2`; four squashed commits of one PR).

### Behavior
New built-in `terminal.backend: kubernetes`. One pod per (profile, session), named `hermes-<profile>-<10 hex>` (at most 63 chars), labelled `app.kubernetes.io/managed-by=hermes` and `hermes/profile`, reused while running and recreated when missing, failed or terminating. A bounded Ready wait (300 s) fails fast with `EnvironmentConnectionError` on image-pull or container-config errors. The backend never creates, resizes or deletes a PersistentVolumeClaim, so it needs only pod and pod-exec rights. `workspace_pvc` (optionally `workspace_subpath`, mounted at `workspace_mount`, default `/workspace`) mounts an **existing** claim; unset means an emptyDir. HOME is an emptyDir at `/home/hermes`, so the synced `~/.hermes` never lands on a shared claim.

Pod hardening: `runAsNonRoot`, uid/gid/fsGroup default 1000 (uid 0 refused), no privilege escalation, drop ALL capabilities, `RuntimeDefault` seccomp, no service-account token, requests equal limits (cpu, memory, ephemeral storage). Namespace, image, resources, node selector and tolerations come only from the profile's `terminal.kubernetes` config, never from the model. Auth: explicit `kubeconfig` path, else in-cluster, else `KUBECONFIG`/default (optional `context`). Idle past `idle_timeout` (default 1800 s) or `cleanup()` deletes the pod after a `sync_back`; a `hermes/last-used` annotation is stamped about once a minute for an external reaper. The optional `kubernetes==36.0.0` extra is added (pinned because 37.x needs a newer certifi than core pins).

### How it works
`tools/environments/kubernetes.py` (`KubernetesEnvironment`): commands run through the exec websocket (`kubernetes.stream`) with the base class's snapshot/cwd wrapping; the wrapper writes a pidfile, length-frames stdin with `head -c`, and exits with the command's status; a timeout kills the whole tree via the pidfile (freeze with SIGSTOP, then KILL); exit code comes from the status channel (255 when the stream closes without one, 137 when cancelled). `_mirror_bearer_token` works around kubernetes 36.0.0 storing the token under `api_key['authorization']` while `auth_settings()` reads `BearerToken`: it mirrors the key on the default configuration and again after every token refresh (a no-op for clients that read `authorization`). A background idle watcher holds only a weak reference. File sync uses tar over exec.

Registration touches the builder table (`_build_kubernetes_env`), requirement checks (`_kubernetes_pre` rejects missing namespace/image; module check names the extra), backend sets (`_CONTAINER_BACKENDS`, `_BUILTIN_BACKENDS`, `BUILTIN_BACKEND_NAMES`, remote-backend sets in prompt builder, env probe, file-path and skills helpers, credential-file home-relative set), default cwd `/workspace`, metrics contract enum, doctor check, setup wizard step, dashboard probe/picker/schema options, Desktop settings enum, defaults, example config, docs, and the extra in `pyproject.toml`, `pm/extras.py`, `nix/packages.nix` and `uv.lock`.

### Code
`tools/environments/kubernetes.py` (new), `tools/terminal_tool_backends.py`, `tools/terminal_tool_config.py`, `tools/terminal_tool.py`, `agent/terminal_env_registry.py`, `agent/prompt_builder.py`, `hermes_cli/config_defaults.py`, `hermes_cli/doctor_tools.py`, `hermes_cli/setup_terminal.py`, `hermes_cli/web_routers/tools.py`, `hermes_cli/web_server_config.py`, `hermes_cli/web_server_profiles.py`, `hermes_cli/observability/shared_metrics_contract.py` (and its v4 schema), `hermes_cli/plugins_registrars.py`, `tools/credential_files.py`, `tools/env_probe.py`, `tools/file_tools_paths.py`, `tools/skills_tool_setup.py`, `pyproject.toml`, `pm/extras.py`, `nix/packages.nix`, `uv.lock`, `cli-config.yaml.example`, `apps/desktop/src/app/settings/constants.ts`, user-guide docs.

### Tests
Source tests inspected, not executed here: `tests/tools/test_kubernetes_environment.py`, using an in-process `FakeCluster`/`FakeExec` (a local process stands in for pod exec) and fake API exceptions. These are unit mocks only; **no live cluster was used or verified in this mirror pass.** Cases: stdout and real exit code (0 and 7); cwd persists across commands; a timed-out command returns 124, `hermes_timed_out`, and a kill exec targets the same pidfile; idle pod is deleted, recreated on the next command (`environment_recreated`), and no PVC API is ever called; profile config drives namespace, image, resources, labels, node selector, tolerations and the full security context; existing claim and subpath mount without PVC calls; workspace mount path stays apart from HOME; run-as ids land in both security contexts; a running pod is reused; missing client names the extra (twice, including the requirement checker and unavailable reason); `fetch_file` streams over exec with a size cap; image-pull failure raises `EnvironmentConnectionError`; a failed pod is deleted and replaced; uid 0 refused with no pod created; default kubeconfig fallback; and `test_kubeconfig_token_reaches_the_request_header_with_the_real_client`, which uses the real client library and skips when the extra is not installed.

### Applying
`git am -3` 1316. Large patch; the `uv.lock` hunk may need regenerating if the base lockfile differs.

### Gotchas
- Needs `pip install hermes-agent[kubernetes]`; the pin matters.
- The first live use hit the 401 token bug fixed in the last squashed commit; the real-client test guards it, but the fake-cluster tests do not exercise real cluster auth.
- An existing claim must already be writable by the configured uid/gid or `fs_group`.
- Shared claims with several pods rely on subpath isolation; the backend does not enforce it.
- Pods are deleted on idle or cleanup, so only the workspace claim keeps data.
- The squash includes a docs/config rewrap of comments in `config_defaults.py` unrelated to Kubernetes.

### Plugin?
No (a built-in backend; the plugin registrar docstring now lists `kubernetes` as a reserved name).

---

## 10. Gated dashboard accepts core session-header service calls (1318)

Patch `1318-fix-dashboard-auth-let-core-session-header-service-c.patch` (fork `b99ddb8156`).

### Behavior
`hermes plugins enable/install` posts `/api/dashboard/agent-plugins/activate`, and the review-retire path reads `/api/health/idle` and posts `/api/health/retirement`, using `X-Hermes-Session-Token` and no cookie. On a gated dashboard (`auth_required`) those were refused as `no_cookie` before the handler ran, so plugin activation never reached open Desktop chats while the CLI printed only the gateway's success. Those exact three paths now accept the deployment session token on a gated app. Every other path, and any request without a valid token, still goes to the cookie/native gates.

CLI reporting: `notify_serve_backend` logs a refused or unreachable backend at WARNING with status and reason (never the token) and returns `{"backend_error": "<status> <reason>"}`. `activation_hint` adds a first line, "The Desktop backend did not load it (401 Unauthorized); open Desktop chats get it after that backend restarts.", or "Desktop backend loaded it ..." when it did. No backend record says nothing new.

### How it works
`register_token_route(path, session_token_check=...)` stores the path in `_session_token_routes`. `authenticate_token` tries `_session_token_principal` first: only when the app is gated, it calls the constant-time check and returns a `TokenPrincipal` with provider `session-token`; a raising check fails closed. `token_auth_middleware` passes a session-token route without a valid token straight to `call_next` rather than answering 401 as a bearer route would. `web_server.py` registers `SESSION_TOKEN_SERVICE_ROUTES` with `_has_valid_session_token`, and `_require_token` accepts that principal on a gated app. `clear_token_routes()` leaves the core session-token routes in place. `plugins_activation.activate_plugin_now` includes a `backend` entry in its result.

### Code
`hermes_cli/dashboard_auth/token_auth.py`, `hermes_cli/web_server.py`, `hermes_cli/plugins_activation.py`.

### Tests
Source tests inspected, not executed here:
- `tests/hermes_cli/test_dashboard_session_token_routes.py` (TestClient on a gated app): valid token without cookie gives 200 `ok` on each of the three routes; wrong token and no token give 401; an unregistered route (`/api/env/reveal`) with the header still gives 401 `no_cookie`; session-token routes survive `clear_token_routes()`; the loopback (ungated) path is unchanged; and a recurrence guard that AST-scans `hermes_cli` and `gateway` for calls sending the session header and asserts each targets a registered session-token route (it also asserts it finds `notify_serve_backend` and `_retirement_request`, so it is not vacuous).
- `tests/hermes_cli/test_plugin_activation_backend_failure.py` (local HTTP server): a 401 backend gives the exact hint text, a WARNING with `401 Unauthorized`, the token never appears in logs or hint, and the gateway half is still reported; a 200 backend shows "Desktop backend loaded it ... hooks."; no backend record leaves the hint unchanged and logs no warning.

### Applying
`git am -3` 1318. Independent of the other ordinals here.

### Gotchas
- A new core caller using the session header on a gated dashboard must be added to `SESSION_TOKEN_SERVICE_ROUTES`; the recurrence test will fail otherwise.
- The in-process self-test caller is exempted in the guard.
- This does not widen cookie-less access beyond the three exact paths, and only for a holder of the session token.

### Plugin?
No (it fixes how core calls the dashboard on behalf of plugin activation).

---

## 11. Respawn-guard holds ordered by event occurrence (1319)

Patch `1319-fix-kanban-order-respawn-holds-by-event-occurrence-d.patch` (fork `397d5e3077`).

### Behavior
The respawn-guard hold clock, reason, notice and cap now follow the time an event occurred, not its local insertion id. Synced history imports old events with newer local ids; the old id-based queries then treated an old claim as the hold's start or hid a fresh hold. A claim, review request or start, spawn, owner-restart loss, or any non-comment event resets the hold. Comments of any author do not restart it, and only non-dispatcher comments still cause a fresh guard event. Timestamps may be integers or ISO strings.

### How it works
`kanban_db_dispatch_guard_notice._current_guard_hold` reads events, converts `created_at` with `kanban_db._to_epoch`, sorts by (time, id) and walks them: a `respawn_guarded` event is appended, any other kind clears the list. `guard_hold_started_at` and `guard_hold_reason` use it. `notify_guard_hold` compares existing `[respawn-guard]` comments after converting their timestamps the same way, so an old ISO-dated notice does not suppress a new hold.

### Code
`hermes_cli/kanban_db_dispatch_guard_notice.py`.

### Tests
Source tests inspected, not executed here: `tests/hermes_cli/test_kanban_guard_chronology.py` (imported history does not trigger notice or cap, for integer and ISO stamps and for both consumers; each lifecycle kind resets the hold even when imported before an older guard; same-second tie starts a fresh hold; owner restart still holds 119 s and releases at 120 s; an old ISO notice does not suppress a new one; a sustained hold notices once and caps once regardless of comment author). `tests/hermes_cli/test_kanban_quota_guard.py` only gains a frozen clock (`kb.time.time`) in two existing tests.

### Applying
`git am -3` 1319. Independent of the other ordinals here.

### Gotchas
Events whose timestamp cannot be parsed are ignored. Sort ties within the same second fall back to id.

### Plugin?
No.

---

## 12. Worker receipt bound and slow-persist logging (1325)

Patch `1325-fix-worker-derive-the-worker-receipt-bound-from-the-.patch` (fork `01a8fe0b30`).

### Behavior
A managed worker's wait for the owner's reply to a `worker.*` RPC is now derived, not a literal 20 s. Previously the owner could wait up to the 60 s transcript write patience for the state database write lock and still commit, while the worker gave up at 20 s with `worker_receipt_timeout` and, never resending, killed a run whose write succeeded. The bound is now the longest owner write patience plus a 30 s margin (90 s today). The owner also logs one WARNING per `worker.persist` slower than 5 s with operation, session, lock wait, total time and outcome.

### How it works
`hermes_state_runtime.worker_receipt_timeout_s()` returns `max(SessionDB._WRITE_PATIENCE_S, SessionDB._TRANSCRIPT_WRITE_PATIENCE_S) + WORKER_RECEIPT_MARGIN_S`. `WorkerRPC` calls it through `_receipt_timeout_s()` for `link.receive`. `apply_worker_receipt` records how long the write waited for the lock into a `WORKER_RECEIPT_TIMING` context variable (the last, successful attempt wins; it is shared with the thread because `asyncio.to_thread` copies context). `gateway/session_worker.py` wraps persist requests in `timed_persist`, which logs the WARNING (`slow worker.persist: operation=... session=... lock_wait=... total=... outcome=...`, `lock_wait=n/a` if the write was never reached) when the total reaches `SLOW_WORKER_PERSIST_S`.

### Code
`hermes_state_runtime.py`, `agent/runtime_session_store.py`, `gateway/session_worker.py`.

### Tests
Source tests inspected, not executed here:
- `tests/agent/test_worker_receipt_bound_contract.py`: the bound equals longest patience plus 30 s; every `receive` timeout a `WorkerRPC` uses for register, adopt and persist is larger than every patience the real handlers pass to `_execute_write` (a spy on a real `SessionDB`); the slow-persist WARNING appears once with operation, session, `lock_wait` (at least 0.4 s and at most total), and `outcome=ok` while another connection holds `BEGIN IMMEDIATE`; a fast persist logs nothing.
- `tests/gateway/test_worker_slow_owner_persist.py` (Linux only): a real owner daemon and worker process, a foreign writer holds the database lock for 25 s mid-turn; the worker exits 0, the assistant row is stored once, no `worker_receipt_timeout`, and the owner log has exactly one slow-persist line with lock wait and total over 20 s and the right session. This test spawns processes and loopback servers; it was not run here.
- `tests/agent/test_worker_rpc_connection_reuse.py`: docstring only.

### Applying
`git am -3` 1325 before 1326 (1326 reuses `_receipt_timeout_s`).

### Gotchas
- Raising a write patience without keeping the margin would reintroduce the problem; the contract test pairs the two bounds.
- A worker now waits up to 90 s on a truly dead owner before failing.
- The commit message claims the tests fail under the mutations (`receive(20)` restored, log line removed); that was not reproduced here.

### Plugin?
No.

---

## 13. WorkerRPC connect steps share the owner-stall budget (1326)

Patch `1326-fix-worker-WorkerRPC-connect-steps-share-the-owner-s.patch` (fork `ff727b789d`).

### Behavior
A worker's first connection to its owner had three independent fixed 5 s deadlines (endpoint discovery, identify, WebSocket upgrade `open_timeout`); one owner delay over 5 s killed the run, and nothing retried. The three steps now share one deadline of `worker_receipt_timeout_s()` and each takes what remains. Waiting is safe because nothing has been sent yet.

### How it works
`WorkerRPC._connection` computes `deadline = time.monotonic() + _receipt_timeout_s()` and passes `remaining()` (never below 0.1 s) to `discover_gateway_endpoint(timeout=...)`, `query_identify(timeout=...)` and `connect(open_timeout=...)`. The WebSocket client also gets `logger=ws_logger()` from `hermes_cli.ws_logging`.

### Code
`agent/runtime_session_store.py`.

### Tests
Source tests inspected, not executed here, in `tests/agent/test_worker_rpc_connection_reuse.py`:
- `test_connect_steps_share_the_receipt_budget_not_a_fixed_five_seconds`: each recorded timeout is within (budget - 5, budget].
- `test_worker_process_survives_an_owner_upgrade_reply_delayed_eight_seconds` (Linux only): a subprocess worker, with discovery, identify and ticket stubbed, still lands its first `worker.persist` when the owner's upgrade reply is stalled 8 s (elapsed at least 7.5 s, one request seen).

### Applying
`git am -3` 1326 after 1325.

### Gotchas
Depends on 1325 (`_receipt_timeout_s`). `ws_logger` must exist in `hermes_cli.ws_logging` in the target base. A truly absent owner still fails after the full budget.

### Plugin?
No.
