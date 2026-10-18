# Sidebar status stripe + gateway device glyph (Desktop)

Patches: `0038`, `0039`, `0044`, `0051`, `0063`, `0070`, `0089`, `0091`, `0094`, `0095`, `0099`, `0104`, `0124` in `core/patches/`. `0038`/`0039` touch `apps/desktop`
only, as does `0089`; `0044`, `0051`, `0063` and `0070` also add backend fields to the session-list endpoints; `0091`
is backend-only (`tui_gateway/server.py`), as is `0094` (`tui_gateway/session_reaper.py`); `0095` touches cron, the
session-list endpoints and `apps/desktop`. `0099` and `0104` are Desktop-only refresh fixes; `0124` touches the
project-tree RPCs and `apps/desktop`. Replaces the per-gateway
row stripe from `0024` ([multi-gateway-projects.md](multi-gateway-projects.md)).

- `0038`: stripe shows session status (green/red); gateway becomes a device glyph after the age.
- `0039`: green stays after you open the row.
- `0044`: a session whose kanban tasks are still queued or running stays "working" (no green).
- `0051`: a session whose `/goal` is still active stays "working" (no green).
- `0063`: the row shows open and blocked kanban task counts after the age.
- `0070`: a session that owns a live background process (per the backend) stays "working" (no green).
- `0089`: the travelling arc border marks the selected row, not every working row.
- `0091`: a turn running longer than 5 min no longer paints red while its tools are still active.
- `0094`: the backend no longer reaps a detached session that owns a running background job.
- `0095`: a session that owns a live cron job shows a grey loop glyph instead of the stripe (never green).
- `0099`: activity-only list refreshes update badges and stripes even when messages have not changed.
- `0104`: a store-level sweep refreshes stale running background rows, including hidden sessions.
- `0124`: project-mode rows older than the recents page get the same activity stamps, so they no longer paint
  green while their `/goal`, cards or background jobs run.
- Red for blocked kanban cards is `0092`, documented in [kanban-session-links.md](kanban-session-links.md).

## Behavior

- The travelling arc border (`.arc-row`) paints only on the **selected** row, the session the pane shows,
  whether it is idle or working (`0089`). Upstream painted it on every running row, so with several sessions
  busy the current one got lost. Working state is still shown by the status dot.
- The 3 px stripe on a session row's left edge shows the session's **status**, not its gateway:
  - **green**: the agent finished its turn and is waiting on you: dot state `unread`, or `idle` with at
    least one message. It stays after you open the row (`0039`; in `0038` it was unread-only).
  - **red**: not finished and needs help (dot state `needs-input`, i.e. blocked on a clarify/approval
    prompt, or `stalled`, i.e. a live turn that has gone quiet). Since `0092` also when kanban cards it created or
    shepherds are `blocked` and no turn is working ([kanban-session-links.md](kanban-session-links.md)).
  - A live turn counts as quiet only when the agent itself has gone quiet (`0091`). Before, the live list's
    `last_active` was the turn's start time, so every turn longer than 5 min painted red while its tools ran, and
    opening the session cleared it (streamed events refreshed the clock), which looked like an instant resume.
  - **grey**: you parked it with ⌘B (`0048`, [session-navigation-shortcuts.md](session-navigation-shortcuts.md)).
    Overrides every other colour; ⌘]/⌘[ and ⌘⇧]/⌘⇧[ skip it. ⌘B again clears it.
  - **loop glyph (grey)**: the session owns an enabled, scheduled cron job (`0095`), e.g. it set up "check X every
    10 minutes". Its work recurs on its own, so it is never "done". A tiny grey `sync` codicon replaces the stripe
    and the row is treated like a parked one: ⌘]/⌘[ skip it. ⌘B (grey bar) and a real prompt (`needs-input`,
    red) beat it; a live turn does not. Paused, disabled or finished jobs don't count.
  - **none**: working, background/delegating, draft, archived, or a session with no turn yet.
- A finished turn does not go green while the session's work still runs elsewhere. Its dot reads
  `background` (no stripe) when:
  - kanban tasks it created are still `todo`, `ready`, `running` or `scheduled` (`0044`). A turn that calls
    `kanban_create` ends at once while workers run for hours. `blocked`, `review`, `done` and `archived`
    tasks don't count.
  - its standing `/goal` is still `active` (`0051`): between goal turns, or parked on a judge `wait` timer.
    Paused, done and cleared goals don't count.
  - it owns a background process (`terminal` with `background=true`) whose PID is still alive, as the backend
    sees it (`0070`). The renderer already showed `background` for processes it had polled itself, but only for
    sessions whose tool events streamed into this window. A turn the backend resumed after a restart, or a
    process registered by another Hermes process, left the row green while e.g. `gh pr checks --watch` still ran.

  All three count over the session's whole compression lineage. Once the work ends the row goes back to green.
  A live turn still wins: the row reads `working`.
- Activity-only list changes now repaint the row (`0099`): kanban counts and goal, cron or background
  activity no longer wait for the session’s own messages to change.
- Hidden sessions with renderer-cached running background rows are re-polled every 30 s (`0104`),
  even when their composer pane is not visible. A confirmed-gone runtime loses its running rows;
  completed rows remain for existing linger/Stop handling. Transient failures retain running rows.
  Removing stale activity allows an otherwise finished session to go green; other busy/blocked/periodic
  state still determines its stripe.
- The row shows kanban task counts right after the age and before the gateway glyph (`0063`): open
  (queued/running) with a project glyph, blocked with an amber `?`. A side that is zero is hidden; with no
  kanban work nothing renders. The tooltip reads e.g. "3 kanban tasks open, 1 blocked".
- The owning gateway becomes a small device glyph right after the row's age (and after any kanban
  counts): laptop, Mac mini or cloud (a generic monitor when nothing matches). Shown only when the registry
  has more than one connection. With the age column off, the counts and glyph are the row's last trailing
  items. Like the age, they fade for the ⋯ button on hover.

- **Project-mode rows past the recents page (`0124`).** In project mode an older session's only loaded row comes
  from the project tree (`projects.tree` previews, `projects.project_sessions` lanes), not from `$sessions`. Those
  rows now carry `goal_active`, `kanban_active`/`kanban_blocked`, `cron_active` and `background_active`, and the
  row's stripe and status dot fold them in, so such a row stays off green while that work runs, like a recents row.

## How it works

- `sessionStatusStripe(state, hasTurns)` in `store/session-dot-state.ts` maps the row's resolved
  `SessionDotState` (the same one the status dot paints) to `'done' | 'stuck' | null`.
- Kanban busy (`0044`): `hermes_cli/kanban_session_activity.py` reads every board db (`kanban.db` under
  `kanban_db.kanban_home()` plus `boards_root()/*/kanban.db`), read-only, and counts tasks per
  `tasks.session_id` in `ACTIVE_STATUSES`. Results are cached for 3 s because the sidebar polls.
  `annotate_kanban_activity(rows)` stamps `kanban_active` (summed over `id`, `_lineage_root_id` and
  `_lineage_ids`) and clears a stale stamp when the count is zero. It never raises. It runs in
  `get_sessions` (`web_routers/sessions.py`) and in `get_profiles_sessions` and
  `get_profiles_sessions_sidebar` (`web_routers/profiles.py`).
- Goal active (`0051`): `hermes_cli/goal_session_activity.py` scans `state_meta` keys `goal:<session_id>`
  in `state.db` of the Hermes home, the default root and every `profiles/*/state.db`, read-only. A goal
  counts when its JSON has `status == "active"` and a non-blank `goal`. Cached 2 s.
  `annotate_goal_activity(rows)` stamps `goal_active: true` by lineage, clears stale stamps, never raises,
  and runs on the same three endpoints right after the kanban stamp.
- Background processes (`0070`): `hermes_cli/process_session_activity.py` collects live processes from this
  process's `tools.process_registry.process_registry` (`_running`, not exited) and from every `processes.json`
  it can find: the Hermes home, each `profiles/*/processes.json`, and the registry's own `_checkpoint_path()`.
  Only host-scoped entries whose PID passes `ProcessRegistry._host_pid_is_ours(pid, host_start_time)` count; an
  in-memory session with a non-host `pid_scope` counts without a PID check. `_owner_ids(item)` maps an entry to
  sessions via `session_key`, `parent_session_id`, `owner_task_id` and `task_id` (a `session:` prefix is stripped;
  an empty or `default` task id is ignored). Results are cached for 3 s (`_CACHE_TTL_S`).
  `annotate_background_activity(rows)` stamps `background_active` with the number of distinct processes owned by
  the row's `id`, `_lineage_root_id` and `_lineage_ids` (a process counts once per row), clears a stale stamp when
  the count is zero, and never raises. It runs on the same three endpoints right after the goal stamp.
  `background_process_counts()` returns the per-session counts.
- Desktop side: `SessionInfo` (`types/hermes.ts`) gains `kanban_active?`, `goal_active?`,
  `kanban_blocked?` and `background_active?` (a number). `$kanbanBusySessionIds`, `$goalActiveSessionIds` and
  `$backgroundActiveSessionIds` (rows with `background_active > 0`) collect ids from `$sessions` and
  `$messagingSessions`. `$sessionDotStateById` claims all three as `background` before `working`, so a live turn
  overrides them. `$backgroundActiveSessionIds` is claimed right after the renderer's own
  `$backgroundRunningSessionIds`; kanban and goal come after `delegating`. The stripe needs no change: `background`
  already maps to no stripe in `sessionStatusStripe`.
- Blocked counts (`0063`): `_read_counts(path, statuses)` now takes the status set; `_all_counts()` reads
  `ACTIVE_STATUSES` and `BLOCKED_STATUSES = ("blocked",)` into two maps under one cache entry.
  `active_task_counts()` and the new `blocked_task_counts()` return each. `annotate_kanban_activity` stamps
  `kanban_blocked` the same way as `kanban_active`. Blocked tasks do not make the session busy.
- `app/chat/sidebar/gateway-device-icon.tsx`: `gatewayDevice(connection, localHostname)` picks the kind.
  `cloud` connections → cloud. Otherwise a name is matched (`mini` → Mac mini, `macbook|laptop|book` →
  laptop): the connection's label + ssh host + id, or for the `local` connection the backend hostname from
  `/api/system/stats` (fetched once, cached in `$localHostname`). The Mac mini glyph is an inline SVG on the
  tabler 24 px stroke grid; laptop is tabler `IconDeviceLaptop` (exported as `Laptop` from `lib/icons.ts`).
- `sidebar/session-row.tsx` renders the stripe (`data-slot="session-status-stripe"`,
  `data-status-stripe="done|stuck"`) and puts the kanban counts and `<GatewayDeviceIcon>` inside the age's
  tail span (or their own trailing span when the age is off). The counts span is
  `data-testid="session-row-kanban-counts"` with `data-kanban-count="open|blocked"` children, labelled by
  the new i18n string `sidebar.row.kanbanTasks(open, blocked)` (English only; other locales are listed in
  `overlay-gaps.json`).

- Detached-session keepalive (`0094`): closing a session runs `AIAgent.close()`, which SIGTERMs its
  non-persisted background jobs. A `notify_on_complete` watcher the chat promised to wake on died silently and the
  row went green. The WS-orphan, TTL and LRU-cap reapers (`tui_gateway/session_reaper.py`,
  `session_lifecycle.py`) now keep such a session until the job exits; memory-trim quiescence is unchanged.
- Periodic (`0095`): `cron.jobs.create_job(owner_session_id=...)` persists the creating session (only when set).
  The `cronjob` tool passes `_owner_session_id(session_id)`: its own session id, else `HERMES_SESSION_ID` via
  `get_session_env`, which a `hermes cron create` run from an agent's terminal inherits.
  `hermes_cli/cron_session_activity.py` reads every `cron/jobs.json` (Hermes home, default root,
  `profiles/*/cron/jobs.json`), re-reading a file only when its mtime/size changes, plus a 2 s TTL. A job counts when
  `enabled`, `state` not paused/completed/disabled, and `next_run_at` set. `annotate_cron_activity(rows)` stamps
  `cron_active: true` by lineage on the same three endpoints, clears stale stamps and never raises. Desktop:
  `SessionInfo.cron_active?`; `sessionStatusStripe(state, hasTurns, userBlocked, kanbanBlocked, periodic)` returns
  `'periodic'` after `blocked` and before everything except `needs-input`; `session-row.tsx` renders the glyph with
  `data-status-stripe="periodic"`; `sessionHasStatusStripe` steps only to `done`/`stuck`.
- Live-list activity clock (`0091`): `session.active_list` builds each row with `_session_live_item`, whose
  `last_active` now comes from `_session_live_last_active(session, now)` in `tui_gateway/server.py`.
  `session["last_active"]` is stamped only at turn start/end; for a `running` session the helper takes the max of
  that stamp, the agent's `_last_activity_ts` (API waits, stream tokens, tool heartbeats) and, for an isolated
  compute-host turn (`_compute_host_turn_id` set), `now` minus the age of `_compute_host_activity_ns`
  (a `perf_counter_ns` stamp), capped at `now`. Idle sessions keep their stored stamp. Desktop's
  `rehydrateLiveSessionStatuses` is unchanged: it still marks a working row `stalled` once `last_active` is 5+ min old.

- `0124`: `tui_gateway/methods_projects.py::_annotate_tree_activity` runs the four session-list annotators
  (`annotate_kanban_activity`, `annotate_goal_activity`, `annotate_cron_activity`, `annotate_background_activity`)
  over the rows `_project_tree_inputs` builds (never raising; the tree renders without them). `build_tree` passes the
  dicts by reference, so previews and hydrated lanes share the stamps; `projects.tree`, `projects.project_sessions`
  and the all-profiles REST tree all go through `_project_tree_inputs`. On the Desktop side `$sessionDotStateById`
  still reads only `$sessions`/`$messagingSessions`; `withRowActivity(state, row)` lifts `idle`/`unread`/`draft` to
  `background` when the row itself carries an activity stamp (louder states win), the same claim and priority the
  store applies.

## Code

Paths below are relative to `apps/desktop/src/` (backend paths above are repository-relative).

- `store/session-dot-state.ts` (`0124`): `withRowActivity`. `app/chat/sidebar/session-row.tsx` and
  `app/chat/session-status-dot.tsx` wrap the store's state with it, so the stripe and the dot agree.

- `lib/session-signatures.ts` (`0099`): `sameCronSignature` now compares `kanban_active`,
  `kanban_blocked`, `goal_active`, `cron_active` and `background_active` alongside content, pin and
  archive fields. A difference prevents the refresh gate from retaining the old `$sessions` array.
- `store/composer-status.ts` (`0104`): `writeBackground` calls `syncRunningSweep`;
  `sessionsWithRunningRows` selects session ids from `$backgroundStatusBySession`. A single
  `RUNNING_SWEEP_MS = 30_000` interval calls `refreshBackgroundProcesses` for each selected id,
  and stops when none remain. This supplements, rather than replaces, visible-pane polling.
- In the same file, `dropRunningRows` filters only `state === 'running'` items.
  `refreshBackgroundProcesses` invokes it both when `isSessionGone(sid)` is already latched and
  after a terminal session-gone error is detected and recorded with `markSessionGone`. The
  backend’s `background_active` stamp still accounts for genuinely live processes.

## Tests

The three 0099/0104 regression files below passed in the full-series verification (October 16, 2026 PT). Earlier feature tests listed afterward were not rerun in this update.

- `lib/session-signatures.test.ts` (`0099`): parameterized “is false when only %s changed”
  covers each of the five activity fields, including removal to `undefined`; unchanged values
  still compare equal.
- `store/composer-status.test.ts` (`0104`), “stale running rows”: re-polls without a pane,
  reconciles a failed exit, makes no sweep requests with only finished rows, drops running rows
  for both newly reported and already-latched gone runtimes, and retains them on a timeout.
- `app/chat/composer/status-stack/polling-guard.test.tsx` (`0104`): the hidden/visible pane
  test uses two 12 s windows below the store’s 30 s sweep, preserving the assertion that hidden
  panes skip their own 5 s timer while visible panes poll.

- `0124` (passed on the full 0001–0127 series, Oct 17, 2026 PT):
  - `tests/tui_gateway/test_projects_rpc.py::test_tree_rows_carry_session_activity_stamps`: `projects.tree`
    previews and `projects.project_sessions` lanes carry `goal_active` and `kanban_active`/`kanban_blocked`; an idle
    session carries none of the four (control). `cron_active` and `background_active` are only checked as absent.
  - `store/session-dot-state.test.ts` "withRowActivity": each stamp lifts idle/unread/draft to background; louder
    states and stamp-less rows are unchanged.
  - `app/chat/sidebar/session-row.test.tsx` "SidebarSessionRow own activity stamps": a row not in `$sessions` with
    `goal_active`, `kanban_active` or `background_active` shows no green stripe; with no stamps it shows done.
  - Untested: the all-profiles REST project tree (same `_project_tree_inputs`, but no test calls it) and the status
    dot's use of `withRowActivity` (only the row stripe is rendered in tests).
- `store/session-dot-state.test.ts`: `sessionStatusStripe`; "kanban work in flight
  (`$kanbanBusySessionIds`)" and "standing goal in flight (`$goalActiveSessionIds`)" (background not done,
  back to done when the work ends, yields to a live turn), and the same three cases in "backend-registered
  background process (`$backgroundActiveSessionIds`)" (`0070`).
- `app/chat/sidebar/gateway-device-icon.test.ts`.
- `app/chat/sidebar/session-row.test.tsx`: "SidebarSessionRow selection arc" (no arc on an unselected settled
  or running row; arc on the selected row, idle or not) (`0089`), and "SidebarSessionRow kanban counts" (counts follow the age,
  zero side hidden, nothing when idle).
- `tests/hermes_cli/test_kanban_session_activity.py`: only pending/running count across boards, lineage
  stamping and stale-stamp clearing, old schema never breaks the list,
  `test_stamps_blocked_count_separately_from_active`.
- `tests/tui_gateway/test_tui_gateway_server.py` (`0091`):
  `test_session_active_list_last_active_follows_running_turn_activity` (running turn follows the agent clock, an
  isolated turn follows its ns stamp, an idle row keeps its stored stamp, a really quiet running turn stays old,
  nothing is in the future).
- `tests/tui_gateway/test_detached_loop_keepalive.py` (`0094`).
- `0095`: `store/session-dot-state.test.ts` "paints periodic (grey loop) ..."; `tests/hermes_cli/test_cron_session_activity.py`
  (only live owned jobs count across profiles, lineage stamping, corrupt file harmless);
  `tests/tools/test_cronjob_tools.py::TestUnifiedCronjobTool::test_create_records_owner_session`.
- `tests/hermes_cli/test_goal_session_activity.py`: only active goals count across profiles, lineage
  stamping, missing `state_meta` is harmless.
- `tests/hermes_cli/test_process_session_activity.py` (`0070`): a live process in `processes.json` stamps its
  session while a dead PID clears a stale stamp; lineage ids and a `session:<id>` task id count each process
  once; the in-process registry counts.

## Applying

Use a disposable checkout at the fork/upstream merge-base and apply the numbered series in order
through `0104` for the complete behavior here. The full series through `0105` applied cleanly;
no isolated-apply validation is claimed. Rebuild Desktop afterward.

Earlier dependency notes (not a minimal apply recipe): name your gateways so the regexes match (e.g. a connection labelled `macbook`, or
an ssh host containing `mini`). Apply `0044` before `0051` and `0063`: both edit code it adds in
`kanban_session_activity.py`, `session-dot-state.ts` and `types/hermes.ts`. `0063` also edits the
subscriber-counting part of `_read_counts` and sits after a test, both added by `0059` (sessions that
shepherd a kanban card subscribe to it; not covered here), so apply `0059` first. Apply `0070` after `0051`:
its hunks sit next to the goal code in `session-dot-state.ts`, `types/hermes.ts` and both routers. `0070` reads
`processes.json`, so it works best with `0069` ([process-readoption.md](process-readoption.md)), which stops
Hermes processes from erasing each other's entries there; it does not need `0069` to apply. `0089` is a two-file change to `session-row.tsx` and its test; it drops the row's use of `showsRunningArc` but leaves that helper in `session-dot-state.ts`. `0091` changes one helper in `tui_gateway/server.py` plus a test and needs no Desktop change.
`0094` changes the backend reaper. Apply `0095` after `0092`
(it extends `sessionStatusStripe`'s `kanbanBlocked` signature) and after `0051`/`0070` (adjacent router lines).
`0124` needs the four annotators from `0044`/`0051`/`0070`/`0095` and the project tree from
[multi-gateway-projects.md](multi-gateway-projects.md). Verified: 0001–0127 apply with `git am -3` at merge-base
`4957bbd0a0` (Oct 17, 2026 PT).

## Gotchas

- `0095` only knows jobs created after it landed: older jobs have no `owner_session_id`. Set it by hand in
  `cron/jobs.json` to mark an existing session periodic.

- After `0089`, a running row looks the same as an idle one except for its status dot. If you want the arc on
  working rows back, revert `0089` alone.
- Gateway colors (filter menu) still exist and still color project headers, but no longer paint rows.
- An unmatched local hostname shows the monitor glyph; rename the host or extend the regexes.
- Kanban and goal state come from the session-list poll, so the row can lag by the poll interval plus the
  2–3 s backend cache.
- Tasks count for the session that created them (and, after `0059`, tui sessions subscribed to them). A
  board db with an older schema (no `session_id`) is skipped with a debug log.
- A session with only blocked tasks shows the amber count and, since `0092`, the red stripe (it can't finish until
  someone unblocks them). Before `0092` it went green.
- `0091` only helps when the agent stamps `_last_activity_ts`. A tool that blocks for 5+ min without heartbeats
  still reads as stalled; that is the signal working as intended.
- Any live background process keeps the row off green, including a long-lived server or watcher you meant to
  leave running. After it exits, the backend stamp clears on the list poll plus the 3 s cache;
  a cached renderer running row may additionally wait for the 30 s sweep (`0104`).
- The sweep only revisits sessions already holding running rows; it is not a discovery scan of
  all sessions. A watch-pattern-only process can exit without a process-status event, which is why
  the sweep is needed. A timeout is not evidence that a runtime is gone.
- Background ownership is only as good as `processes.json`: without `0069`, a process missing from the file
  and not in this backend's registry leaves the row green.
- The `kanbanTasks` tooltip is untranslated outside English.
- `0124`: a project-tree row's stamps refresh with the tree fetch, not the session-list poll, so they can lag
  longer than a recents row's.

## Could this be a plugin instead?

Not as a replacement for these fixes. The built-in stripe depends on core session-list comparison,
row state and composer process reconciliation. `0099` changes the equality gate that admits new
activity stamps; `0104` changes the shared background store and gone-runtime handling. A plugin
can display a separate indicator but cannot fix those built-in refresh paths through a UI contribution.
