# Sidebar status stripe + gateway device glyph (Desktop)

Patches: `0038`, `0039`, `0044`, `0051`, `0063` in `core/patches/`. `0038`/`0039` touch `apps/desktop` only;
`0044`, `0051` and `0063` also add backend fields to the session-list endpoints. Replaces the per-gateway
row stripe from `0024` ([multi-gateway-projects.md](multi-gateway-projects.md)).

- `0038`: stripe shows session status (green/red); gateway becomes a device glyph after the age.
- `0039`: green stays after you open the row.
- `0044`: a session whose kanban tasks are still queued or running stays "working" (no green).
- `0051`: a session whose `/goal` is still active stays "working" (no green).
- `0063`: the row shows open and blocked kanban task counts after the age.

## Behavior

- The 3 px stripe on a session row's left edge shows the session's **status**, not its gateway:
  - **green**: the agent finished its turn and is waiting on you: dot state `unread`, or `idle` with at
    least one message. It stays after you open the row (`0039`; in `0038` it was unread-only).
  - **red**: not finished and needs help (dot state `needs-input`, i.e. blocked on a clarify/approval
    prompt, or `stalled`, i.e. a live turn that has gone quiet).
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

  Both count over the session's whole compression lineage. Once the work ends the row goes back to green.
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
- Desktop side: `SessionInfo` (`types/hermes.ts`) gains `kanban_active?`, `goal_active?` and
  `kanban_blocked?`. `$kanbanBusySessionIds` and `$goalActiveSessionIds` collect ids from `$sessions` and
  `$messagingSessions`. `$sessionDotStateById` claims both as `background`, after `delegating` and before
  `working`, so a live turn overrides them.
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

## Tests

- `store/session-dot-state.test.ts`: `sessionStatusStripe`; "kanban work in flight
  (`$kanbanBusySessionIds`)" and "standing goal in flight (`$goalActiveSessionIds`)" (background not done,
  back to done when the work ends, yields to a live turn).
- `app/chat/sidebar/gateway-device-icon.test.ts`.
- `app/chat/sidebar/session-row.test.tsx`: "SidebarSessionRow kanban counts" (counts follow the age,
  zero side hidden, nothing when idle).
- `tests/hermes_cli/test_kanban_session_activity.py`: only pending/running count across boards, lineage
  stamping and stale-stamp clearing, old schema never breaks the list,
  `test_stamps_blocked_count_separately_from_active`.
- `tests/hermes_cli/test_goal_session_activity.py`: only active goals count across profiles, lineage
  stamping, missing `state_meta` is harmless.

## Applying

Apply after `0024`. Name your gateways so the regexes match (e.g. a connection labelled `macbook`, or
an ssh host containing `mini`). Apply `0044` before `0051` and `0063`: both edit code it adds in
`kanban_session_activity.py`, `session-dot-state.ts` and `types/hermes.ts`. `0063` also edits the
subscriber-counting part of `_read_counts` and sits after a test, both added by `0059` (sessions that
shepherd a kanban card subscribe to it; not covered here), so apply `0059` first.

## Gotchas

- Gateway colors (filter menu) still exist and still color project headers, but no longer paint rows.
- An unmatched local hostname shows the monitor glyph; rename the host or extend the regexes.
- Kanban and goal state come from the session-list poll, so the row can lag by the poll interval plus the
  2–3 s backend cache.
- Tasks count for the session that created them (and, after `0059`, tui sessions subscribed to them). A
  board db with an older schema (no `session_id`) is skipped with a debug log.
- A session with only blocked tasks shows the amber count but still goes green: blocked work waits on you.
- The `kanbanTasks` tooltip is untranslated outside English.
