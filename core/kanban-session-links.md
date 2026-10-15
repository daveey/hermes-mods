# Chats stay linked to the Kanban cards they create or shepherd

Patches:

- `patches/0059-fix-kanban-sessions-shepherding-an-existing-card-sub.patch`: tools. A chat that comments on,
  unblocks or links a card it did not create subscribes to it, behind `kanban.auto_subscribe_on_touch` (default
  off). The sidebar counts subscribed cards as the session's active work.
- `patches/0067-fix-kanban-CLI-create-stamps-the-calling-chat-sessio.patch`: CLI. `hermes kanban create` stamps
  the calling chat's session (`HERMES_SESSION_ID`) on the new card.
- `patches/0068-fix-kanban-CLI-create-in-an-agent-terminal-stamps-HE.patch`: CLI. In a Desktop/TUI agent terminal,
  `hermes kanban create` prefers `HERMES_SESSION_KEY` and also adds the `tui` completion subscription.

What you see: a chat that starts or babysits Kanban cards stays tied to them. Ask a chat to "make sure t_x finishes"
and, with the config flag on, it wakes when the card completes or blocks. Its sidebar row stays busy until the card
lands instead of painting green "done" while the card still runs. Cards an agent creates by shelling out to
`hermes kanban create` (terminal or execute_code) now show in that chat's Tasks view, the same as cards made with
the `kanban_create` tool. Before, the chat said "N cards running" while its Tasks view was empty.

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

## Gotchas

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

## Plugin?

No. The subscribe calls live inside the Kanban tool handlers and `hermes kanban create`, and the sidebar count is in
the session-list code. None of these have plugin hooks.
