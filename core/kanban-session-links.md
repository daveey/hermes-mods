# Chats stay linked to the Kanban cards they create or shepherd

Patches:

- `patches/0059-fix-kanban-sessions-shepherding-an-existing-card-sub.patch`: tools. A chat that comments on,
  unblocks or links a card it did not create subscribes to it, behind `kanban.auto_subscribe_on_touch` (default
  off). The sidebar counts subscribed cards as the session's active work.
- `patches/0067-fix-kanban-CLI-create-stamps-the-calling-chat-sessio.patch`: CLI. `hermes kanban create` stamps
  the calling chat's session (`HERMES_SESSION_ID`) on the new card.
- `patches/0068-fix-kanban-CLI-create-in-an-agent-terminal-stamps-HE.patch`: CLI. In a Desktop/TUI agent terminal,
  `hermes kanban create` prefers `HERMES_SESSION_KEY` and also adds the `tui` completion subscription.
- `patches/0092-fix-desktop-red-stripe-on-a-session-whose-kanban-car.patch`: Desktop. A session whose kanban cards
  are `blocked` gets the red (stuck) sidebar stripe instead of green.
- `patches/0093-feat-kanban-GET-api-session-kanban-tasks-lists-the-c.patch`: backend. `GET
  /api/session-kanban-tasks?ids=…` lists the cards behind a sidebar row's kanban counts.
- `patches/0111-daveey-execute_code-bridge-HERMES_SESSION_ID-KEY-int.patch`: tools. `execute_code`'s sandbox child
  gets the turn's `HERMES_SESSION_ID` / `HERMES_SESSION_KEY`, so `hermes kanban create` run from a script stamps
  and subscribes the chat too (0067/0068 alone only covered the terminal tool).

What you see: a chat that starts or babysits Kanban cards stays tied to them. Ask a chat to "make sure t_x finishes"
and, with the config flag on, it wakes when the card completes or blocks. Its sidebar row stays busy until the card
lands instead of painting green "done" while the card still runs. Cards an agent creates by shelling out to
`hermes kanban create` (terminal or execute_code) now show in that chat's Tasks view, the same as cards made with
the `kanban_create` tool. Before, the chat said "N cards running" while its Tasks view was empty. A chat whose cards
are blocked on a human shows red in the sidebar, and the open chat's task strip lists exactly the cards its badge counts.

## Behavior

- **Shepherding an existing card (0059).** With `kanban.auto_subscribe_on_touch: true`, a chat session that calls
  `kanban_comment`, `kanban_unblock` or `kanban_link` gets the same notify subscription `kanban_create` adds. For a
  link, the child card is the one subscribed. The comment and unblock results include `"subscribed": true` when a
  row was written. Repeat touches don't add duplicate rows.
- **Dispatcher workers never subscribe this way.** If `HERMES_KANBAN_TASK` is set, comments are a handoff channel,
  not shepherding.
- **The flag is off by default**, matching upstream. `kanban.auto_subscribe_on_create` (default on) still governs
  `kanban_create`.
- **Sidebar busy state (0059).** `kanban_active` for a session now counts active cards it created plus active cards
  it has a `tui` notify subscription to. Each (session, card) pair counts once, so a creator's own auto-subscription
  isn't double counted. Active means `todo`, `ready`, `running` or `scheduled`. Gateway chat subscriptions
  (Telegram etc.) are ignored, since those chats are not sidebar sessions. Finished cards keep nobody busy.
- **CLI create stamps the chat (0067, 0068).** `hermes kanban create` sets the card's `session_id` from
  `HERMES_SESSION_KEY`, falling back to `HERMES_SESSION_ID`. Either is used only if that session exists in this
  profile's state.db. A plain user shell has neither, so its cards stay unstamped.
- **CLI create subscribes the chat (0068).** When `HERMES_SESSION_KEY` is set, the CLI also adds the `tui`
  completion subscription, so the chat hears when the card lands and its row stays busy.
- **Workers are excluded.** Inside a dispatcher-owned worker the CLI stamps no session and adds no subscription.
  The card gets `creator_task_id` and inherits its creator task's session instead.
- **Idempotency hits are left alone (0068).** If `--idempotency-key` matches an existing non-archived card, the CLI
  returns it without re-stamping or re-subscribing.
- **execute_code too (0111).** The sandbox scrub drops every non-allowlisted `HERMES_*` var, so before 0111 a script
  that ran `subprocess.run('hermes kanban create …')` made unstamped cards: the chat reported them running while its
  task strip and sidebar badge showed nothing. 0111 bridges just the two session ids into the child.

- **Blocked cards paint red (0092).** If any card the session created or shepherds (its `kanban_blocked` count, from
  0063) is `blocked`, the sidebar stripe is red (`stuck`) unless a turn is actively `working`. A ⌘B user block
  (grey) still wins. ⌘]/⌘[ stepping uses the same rule, so it lands on these rows.
- **Session task list (0093).** `GET /api/session-kanban-tasks?ids=a,b,c` takes one compression lineage's session
  ids (max 50, query max 4000 chars) and returns `{"tasks": [...]}`: every `todo`/`ready`/`running`/`scheduled`,
  `blocked` or `review` card on every board that those ids created or are `tui` subscribers of. Each task has `id`,
  `title`, `status`, `assignee`, `session_id`, `created_at`, `started_at`, `current_run_started_at`, `board` (slug,
  `default` for the root board) and `via` (`created` or `subscribed`; created wins). Missing `ids` is a 422.
  The consumer is the `desktop-plugins/session-tasks` plugin in this repo, which falls back to scanning the
  current board on backends without the route.

## How it works

- 0059, `tools/kanban_tools.py`: `_maybe_auto_subscribe` takes `gate` and `gate_default` keyword arguments, so the
  config key it checks is now `kanban.<gate>`. The new `_maybe_subscribe_on_touch` returns False when
  `HERMES_KANBAN_TASK` is set, otherwise calls `_maybe_auto_subscribe(..., gate="auto_subscribe_on_touch",
  gate_default=False)`. `_handle_comment`, `_handle_unblock` and `_handle_link` call it after their board write. The
  subscription target comes from the existing `_resolve_notify_target`, which maps `HERMES_SESSION_KEY` to platform
  `tui`. Failures are logged and swallowed, as before.
- 0059, `hermes_cli/config_defaults.py`: adds `auto_subscribe_on_touch: False` under `kanban`.
- 0059, `hermes_cli/kanban_session_activity.py`: `_read_counts` builds a set of (session, task) pairs from
  `tasks.session_id` and from `kanban_notify_subs` rows with `platform = 'tui'` joined to active tasks (`chat_id` is
  the session). It then counts pairs per session. A board without the `kanban_notify_subs` table is tolerated.
- 0067, `hermes_cli/kanban.py` `_cmd_create`: computes `in_worker` (`HERMES_KANBAN_TASK` set and
  `is_dispatcher_owned_worker_context()`). Outside a worker it passes `session_id` from
  `tools.kanban_tools._persisted_session_id`, which returns the id only if state.db has that session.
  `creator_task_id` now uses the same `in_worker` check.
- 0068, same function: reads `HERMES_SESSION_KEY` (blank inside a worker) and tries it first, because the key is what
  the session's notify poller listens on. Before `kb.create_task` it checks for an existing non-archived card with
  the same `idempotency_key`. After the create, if there is a session key and no idempotency hit, it calls
  `_maybe_auto_subscribe(conn, task_id)`, so `kanban.auto_subscribe_on_create` gates it too.
- 0111, `tools/code_execution_env.py`: `_build_child_env` calls `_bridge_session_identity(child_env)`, which copies
  `HERMES_SESSION_ID` and `HERMES_SESSION_KEY` from the `gateway.session_context` ContextVars. Once the session
  context is engaged the ContextVar is authoritative (an unbound/empty value is dropped, never taken from the
  possibly-foreign `os.environ` mirror); an unengaged CLI falls back to `os.environ`. No other session var (chat
  name, user id, …) is bridged, keeping #27303's hardening. Both the per-call and persistent-kernel paths use
  `_build_child_env`; kernels are keyed per owning session, so the snapshot at spawn is that session's.

- 0092, `apps/desktop/src/store/session-dot-state.ts`: `sessionStatusStripe(state, hasTurns, userBlocked, kanbanBlocked = 0)`
  returns `stuck` when `kanbanBlocked > 0 && state !== 'working'` (after the user-blocked check).
  `sidebar/session-row.tsx` passes the row's `kanbanBlocked`; `sessionHasStatusStripe` in
  `store/sidebar-visible-order.ts` passes `session.kanban_blocked`, keeping stripe and stepping on one predicate.
- 0093, `hermes_cli/kanban_session_activity.py`: `LISTED_STATUSES = ACTIVE_STATUSES + BLOCKED_STATUSES + ("review",)`.
  `session_tasks(session_ids, statuses=LISTED_STATUSES)` dedupes the ids and calls `_read_session_tasks` on each
  `_board_db_paths()` db (read-only, 0.5 s timeout; a board that raises `sqlite3.Error` is skipped with a debug
  log). That runs the same two queries `_read_counts` counts: `tasks.session_id IN ids`, then
  `kanban_notify_subs` with `platform = 'tui'` and `chat_id IN ids` joined to `tasks`. Columns missing from an old
  schema come back `NULL`; `current_run_started_at` is read from `task_runs` via `current_run_id` when both exist.
  `_board_slug(path)` names the board. Results are not cached.
- 0093, `hermes_cli/web_routers/sessions.py`: `get_session_kanban_tasks` on `list_router` splits `ids`, keeps the
  first 50 and returns `session_kanban_tasks(wanted)`.

## Tests

- `tests/tools/test_kanban_tools.py` (0059): `test_comment_on_existing_card_subscribes_shepherding_session` (one
  `tui` row, no duplicate on a second comment), `test_comment_does_not_subscribe_when_touch_gate_off`,
  `test_worker_comment_never_subscribes`. Unblock and link have no dedicated tests.
- `tests/hermes_cli/test_kanban_session_activity.py` (0059):
  `test_counts_tasks_a_session_subscribed_to_but_did_not_create` (creator counted once, watcher counted per card,
  done card and Telegram row ignored).
- `tests/hermes_cli/test_kanban_create_session_stamp.py` (new in 0067, extended in 0068):
  `test_cli_create_stamps_calling_session`, `test_cli_create_ignores_unknown_session`,
  `test_cli_create_without_session_env_is_unstamped`, `test_cli_create_in_worker_does_not_stamp_worker_session`,
  `test_cli_create_with_session_key_stamps_and_subscribes`, `test_cli_create_without_session_key_adds_no_subscription`,
  `test_cli_create_idempotency_hit_does_not_restamp`.

- `apps/desktop/src/store/session-dot-state.test.ts` (0092): "paints stuck (red) when kanban cards the session
  created are blocked, unless a turn is working" (also: user block wins, zero blocked stays done).
- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx` (0092): "paints the red (stuck) stripe while a kanban
  card it created is blocked".
- `tests/hermes_cli/test_kanban_session_activity.py` (0093):
  `test_session_tasks_lists_created_and_shepherded_cards_the_sidebar_counts` (created and `tui`-shepherded cards
  across two boards, done and non-`tui` rows excluded, matches the badge counts),
  `test_session_tasks_empty_ids_and_old_schema`, `test_session_kanban_tasks_endpoint` (filters by id, 422 without
  `ids`).

- `tests/tools/test_code_execution_session_identity.py` (0111): bound ids reach the child and other session vars
  don't; an engaged-but-cleared context doesn't leak the `os.environ` mirror; an unengaged CLI falls back to it.
  Two of the three fail without the patch.

## Applying

- 0059 modifies `hermes_cli/kanban_session_activity.py` and the `kanban_active` count, which 0044 added (sidebar
  keeps a session working while its Kanban tasks run). Apply 0044 first.
- 0067 and 0068 go in order; 0068 edits lines 0067 added. 0068's tests call
  `kanban_session_activity._reset_cache_for_tests()` and `active_task_counts()`, so they also need 0044, and the
  `kanban_active` assertion relies on 0059's subscription counting.
- To turn on shepherding, set in config.yaml:

  ```yaml
  kanban:
    auto_subscribe_on_touch: true
  ```

- 0092 needs 0063 (the `kanban_blocked` field and `sessionStatusStripe`'s stripe from
  [sidebar-status-stripe.md](sidebar-status-stripe.md)) and the ⌘B user-block argument from 0048.
- 0093 needs 0044 and 0063 (`ACTIVE_STATUSES`, `BLOCKED_STATUSES`, `_board_db_paths`) and inserts its route next
  to the import 0044 added in `web_routers/sessions.py`. Its subscriber query matches 0059's counting; without
  0059 the counts and the list disagree on shepherded cards.

- 0111 is independent of the others (it only adds env vars); without 0067/0068 the CLI ignores them.

## Gotchas

- Cards created before 0111 from execute_code stay unstamped. Backfill: set `tasks.session_id` and call
  `_maybe_auto_subscribe` with `HERMES_SESSION_KEY` set to the chat's id.

- `auto_subscribe_on_touch` is off by default, so 0059's subscribe-on-touch does nothing until you enable it. The
  sidebar change (counting `tui` subscriptions) is always on.
- `kanban_link` subscribes the child card but its result has no `subscribed` field, unlike comment and unblock.
- Any chat session that comments on a card gets subscribed while the flag is on, even a one-off "fyi" comment.
  It stays busy in the sidebar until that card finishes.
- The CLI only subscribes when `HERMES_SESSION_KEY` is set. A session stamped via `HERMES_SESSION_ID` alone gets the
  card in its Tasks view but no completion wake. That's deliberate: `HERMES_SESSION_ID` is set for every CLI/ACP
  run, and `_resolve_notify_target` does not use it as a fallback.
- The idempotency check is a separate query before `create_task`. It isn't atomic with the create, which only matters
  if two creates with the same key race.

- After 0092 a session goes red for blocked cards even when its own turn finished fine; unblock (or archive) the
  card to clear it. A `review` card does not turn it red, but 0093 lists it.
- 0093 lists `review` cards that the sidebar badge does not count, so the strip can show more cards than the badge.
- The endpoint reads only this backend's boards. The desktop plugin asks the active connection, so cards on another
  gateway's boards don't show.

## Plugin?

Partly. 0093's consumer is the `desktop-plugins/session-tasks` plugin in this repo; the route itself must be
in core, since it reads the session-list module. Otherwise no. The subscribe calls live inside the Kanban tool handlers and `hermes kanban create`, and the sidebar count is in
the session-list code. None of these have plugin hooks.
