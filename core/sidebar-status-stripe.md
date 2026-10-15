# Sidebar status stripe + gateway device glyph (Desktop)

Patches: `0038`, `0039`, `0044`, `0051`, `0063`, `0070`, `0089`, `0091` in `core/patches/`. `0038`/`0039` touch `apps/desktop`
only, as does `0089`; `0044`, `0051`, `0063` and `0070` also add backend fields to the session-list endpoints; `0091`
is backend-only (`tui_gateway/server.py`). Replaces the per-gateway
row stripe from `0024` ([multi-gateway-projects.md](multi-gateway-projects.md)).

- `0038`: stripe shows session status (green/red); gateway becomes a device glyph after the age.
- `0039`: green stays after you open the row.
- `0044`: a session whose kanban tasks are still queued or running stays "working" (no green).
- `0051`: a session whose `/goal` is still active stays "working" (no green).
- `0063`: the row shows open and blocked kanban task counts after the age.
- `0070`: a session that owns a live background process (per the backend) stays "working" (no green).
- `0089`: the travelling arc border marks the selected row, not every working row.
- `0091`: a turn running longer than 5 min no longer paints red while its tools are still active.
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
- The row shows kanban task counts right after the age and before the gateway glyph (`0063`): open
  (queued/running) with a project glyph, blocked with an amber `?`. A side that is zero is hidden; with no
  kanban work nothing renders. The tooltip reads e.g. "3 kanban tasks open, 1 blocked".
- The owning gateway becomes a small device glyph right after the row's age (and after any kanban
  counts): laptop, Mac mini or cloud (a generic monitor when nothing matches). Shown only when the registry
  has more than one connection. With the age column off, the counts and glyph are the row's last trailing
  items. Like the age, they fade for the ⋯ button on hover.

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

- Live-list activity clock (`0091`): `session.active_list` builds each row with `_session_live_item`, whose
  `last_active` now comes from `_session_live_last_active(session, now)` in `tui_gateway/server.py`.
  `session["last_active"]` is stamped only at turn start/end; for a `running` session the helper takes the max of
  that stamp, the agent's `_last_activity_ts` (API waits, stream tokens, tool heartbeats) and, for an isolated
  compute-host turn (`_compute_host_turn_id` set), `now` minus the age of `_compute_host_activity_ns`
  (a `perf_counter_ns` stamp), capped at `now`. Idle sessions keep their stored stamp. Desktop's
  `rehydrateLiveSessionStatuses` is unchanged: it still marks a working row `stalled` once `last_active` is 5+ min old.

## Tests

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
- `tests/hermes_cli/test_goal_session_activity.py`: only active goals count across profiles, lineage
  stamping, missing `state_meta` is harmless.
- `tests/hermes_cli/test_process_session_activity.py` (`0070`): a live process in `processes.json` stamps its
  session while a dead PID clears a stale stamp; lineage ids and a `session:<id>` task id count each process
  once; the in-process registry counts.

## Applying

Apply after `0024`. Name your gateways so the regexes match (e.g. a connection labelled `macbook`, or
an ssh host containing `mini`). Apply `0044` before `0051` and `0063`: both edit code it adds in
`kanban_session_activity.py`, `session-dot-state.ts` and `types/hermes.ts`. `0063` also edits the
subscriber-counting part of `_read_counts` and sits after a test, both added by `0059` (sessions that
shepherd a kanban card subscribe to it; not covered here), so apply `0059` first. Apply `0070` after `0051`:
its hunks sit next to the goal code in `session-dot-state.ts`, `types/hermes.ts` and both routers. `0070` reads
`processes.json`, so it works best with `0069` ([process-readoption.md](process-readoption.md)), which stops
Hermes processes from erasing each other's entries there; it does not need `0069` to apply. `0089` is a two-file change to `session-row.tsx` and its test and applies on
its own; it drops the row's use of `showsRunningArc` but leaves that helper in `session-dot-state.ts`. `0091` applies
on its own (one helper in `tui_gateway/server.py` plus a test) and needs no Desktop change.

## Gotchas

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
  leave running. Kill it (or let it exit) and the row goes green within the poll interval plus the 3 s cache.
- Background ownership is only as good as `processes.json`: without `0069`, a process missing from the file
  and not in this backend's registry leaves the row green.
- The `kanbanTasks` tooltip is untranslated outside English.
