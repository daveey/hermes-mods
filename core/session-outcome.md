# Stored chat outcome, server-side mute and sidebar stripe

Patches:

- `patches/1005-feat-sessions-stored-chat-outcome-server-side-mute-t.patch`
- `patches/1006-fix-sessions-outcome-refresh-off-the-messaging-reply.patch`
- `patches/1007-feat-sessions-outcome-refresh-on-notices-and-card-mo.patch`
- `patches/1008-feat-desktop-paint-the-sidebar-from-the-stored-sessi.patch`
- `patches/1009-feat-desktop-push-old-local-B-parks-to-the-backend-o.patch`
- `patches/1010-fix-desktop-unmute-clears-the-stale-muted-outcome-bo.patch`
- `patches/1011-refactor-desktop-bring-session-outcome-under-the-cod.patch`
- `patches/1015-fix-sessions-CLI-and-web-outcome-refresh-load-plugin.patch`
- `patches/1077-fix-tui-reach-split-module-helpers-through-server-so.patch`
- `patches/1079-fix-sessions-notice-guarantee-judges-remote-notices-.patch`
- `patches/1496-fix-tui_gateway-close-agent-init-failed-turns-with-a.patch`

Every chat needs one shared answer to "is this done, waiting on me, or stuck?" instead of each surface re-deriving it from the transcript. This series stores that answer on the session row and paints the Desktop sidebar from it.

## Behavior

- `sessions.outcome_json` / `outcome_at` hold one outcome per chat, and `sessions.muted` is a lineage-wide mute flag. `hermes_state_outcome` derives it by contract precedence: muted, live turn, open decisions, a lost request, live work, a held declaration, tail shape, summary judge/heuristic, empty.
- A lost request outranks live work: a typed or steered message that ended on a failed turn (`turn_failed`), or that nothing answered (`interrupted`), paints amber even with open cards, unless the turn declared its outcome. Automated rows after the failure do not close it, a real reply does, and an interrupted automated delivery alone is not one.
- A recurring cron is not a wait. A finished chat that owns one is `done` and names it in `crons`: the Desktop draws the loop glyph in green and the tooltip says what keeps running. A declared wait on a cron is still honoured.
- A turn refused for a retryable reason keeps its turn marker and is replayed through auto-continue after 45 s, then 180 s (`_hold_refused_turn`, `_schedule_refused_turn_retry`), with the auto-continue guards; a skewed backend leaves the marker for the boot sweep. Person-written turns and their replays only. A later refused message joins the earlier prompt; a later message that succeeds ends the replay (the model has the earlier row in its history). Plugins add a judge and providers.
- It refreshes at every Desktop/TUI turn conclusion, every messaging-platform turn end (on a daemon thread, off the reply path), on orphan reap, on kanban notices and card moves, and from CLI/web refresh.
- A judged outcome stores `judged=true` and the judged message id; a refresh in a process with no judge keeps that verdict for the same message. Judges are called as `fn(sid, text, source=...)`, falling back to `fn(sid, text)`.
- Notice guarantee: a chat ending on a kanban notice older than 5 min with no live turn gets one close-out wake (claimed by CAS, 2 per hour per lineage). A notice about a card blocked on a human never wakes and refreshes to decision. Earlier "blocked" notices are marked `display_metadata.stale` once the card leaves blocked. No refresh for released links, archived chats or moved copies.
- `desktop.auto_continue.resume_on_startup` defaults on.
- Surfaces: list rows carry `outcome` and `muted`; `POST /api/sessions/{id}/mute`; `GET /api/sessions/{id}/outcome?refresh=1`; `hermes sessions mute|unmute|outcome|outcome-audit`. CLI and web refresh load plugin providers and the judge first, so the audit cron cannot store `done` over a chat waiting on a user decision.
- Desktop: `sessionStatusStripe` prefers `row.outcome` (done green, decision red, muted grey, waiting/running grey glyph, unresolved amber); a live turn and a local park still win. The stripe tooltip shows the summary, open questions or the unresolved reason. The park hotkey writes through the mute endpoint, keeps the local list as an optimistic cache and migrates old local parks once per profile and gateway. Unmute drops the stale `muted` outcome; automated rows ignore stored outcomes. Stale notices render dimmed as "outdated: card moved on".

## How it works

`hermes_state_outcome.py` derives, stores and refreshes; the gateway, TUI gateway, reaper and notice paths call the refresh. `tui_gateway/notice_guarantee.py`, `notice_fanout.py` and `reap_notices.py` implement the wake and fan-out filter. Desktop code moves the stripe into `sidebar/session-row-stripe.tsx`, outcome types into `types/session-outcome.ts` and copy into `i18n/en_session_row_status.ts` (1011, no behavior change).

## Code

`hermes_state_outcome.py`, `hermes_state_sessions.py`, `gateway/turn_outcome.py`, `tui_gateway/turn_outcome.py`, `tui_gateway/notice_*.py`, `hermes_cli/sessions_cmd_outcome.py`, `hermes_cli/web_routers/sessions.py`, `apps/desktop/src/app/chat/sidebar/session-row*.tsx`, `apps/desktop/src/store/session-blocked.ts`.

## Tests

Test files added or changed by these commits (not re-run for this mirror): `tests/hermes_state/test_session_outcome.py`, `test_session_outcome_judge.py`, `tests/tui_gateway/test_refused_turn_retry.py`, desktop `session-outcome.test.ts`, `tests/gateway/test_turn_outcome_refresh.py`, `tests/tui_gateway/test_turn_outcome_refresh.py`, `test_session_outcome_restarts.py`, `tests/hermes_cli/test_session_outcome_surfaces.py`, desktop `session-row.test.tsx`, `session-mute.test.ts`, `session-blocked.test.ts`, `sidebar-automated.test.ts`, `use-session-list-actions.test.tsx`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

Older backends lack `muted`/`outcome`; the Desktop keeps the local park list as the only record then. The session schema gains columns (`session_schema_history.py`).

## Plugin

The user-steps plugin supplies the decision provider; a judge plugin is optional.

## Follow-up patches (1077, 1079)

- **1077** (`7ae59acf25c`): `turn_outcome.py` imported `_session_db` straight from `session_workdir`, but split `tui_gateway` modules are written against `server.py`'s globals (`bind_module` rebinds them there), so the raw copy raised `NameError: _get_db` on every Desktop/TUI turn end and the stored outcome never refreshed (the old test hid it by stubbing the module's `_get_db`). `turn_outcome`, `kanban_unloaded`, `notice_guarantee`, `kanban_remote_notices` and `methods_bot_relay` now reach split helpers through `server`; the same bug had `session_notifications._readopt_wake_profiles` return only the launch store, so the unloaded sweep and the notice guarantee never searched named profiles' stores. Tests: `tests/tui_gateway/test_split_module_imports.py` (3; an AST guard failing on any direct import of a `bind_module` split module's function from another tui_gateway module), `test_turn_outcome_refresh.py` (3, adds `test_refresh_uses_server_bound_session_db`), busy/wake tests now patch server's helpers.
- **1079** (`0a2eb8797bf`, notice guarantee for remote notices; extends the guarantee from 1007): a notice about a card on another gateway stores the OWNER's board name plus `gateway=<owner>`, and `blocked_on_human` opened that board locally. `kanban_db_connect` refused ("kanban board bb does not exist"), `guarantee_pass` logged and moved on without marking the row, and the same notice was retried every pass (349 warnings in one window) while the chat never got a close-out. Now: both remote-notice writers (sweep metadata, collect items) record `blocked_on_human` from the replicated task (`remote_blocked_on_human`: status / block_kind / human_request) and the open-chat poller copies it onto its persisted row; `notice_guarantee.blocked_on_human` returns that stored flag for a notice with a foreign `gateway` or only `remote:` event keys and never opens a local board; a local notice whose board is gone reads as not blocked; a candidate whose handling raises is stamped `closeout.state = "error"` once (compare-and-swap, `_give_up`) and its outcome refreshed (`unresolved{notice_tail}`) for the auditor instead of being retried forever. Tests: `tests/tui_gateway/test_notice_guarantee.py` (9: remote flag and no local board, only-remote-keys poller row, missing board still closes out, stamped error once), `tests/tui_gateway/test_kanban_remote_notices.py` (22, covers the stored flag). Tests were added or changed by these commits; they were not re-run for this mirror.

## Follow-up patch (1496): a turn whose agent never started reads `turn_failed`

**1496** (`b99f7b9137`, `fix(tui_gateway): close agent-init-failed turns with a failed_turn boundary and refresh the outcome`).

- **Behavior.** A Desktop/TUI prompt whose deferred agent build fails (e.g. a `ModelRoutingError`, "No eligible deep routes") used to end with only the client's terminal error frame and inflight snapshot. No agent ran, so neither the conversation-loop closer (`agent/conversation_loop.py::_close_durable_failed_turn`) nor the gateway closer wrote the failed-turn boundary: the durable tail stayed the accepted user row and the stored outcome read `unresolved{interrupted}` forever. Now `_run_after_agent_ready` appends one assistant row (`FAILED_TURN_NOTICE`, `display_kind=failed_turn`, `display_metadata.error_surface = {layer: runtime, code: agent_init_failed, retryable: true}` plus the redacted error) and then calls `refresh_turn_outcome`, so the chat reads `unresolved{turn_failed}`. A later retry is a new request, not one `repair_message_sequence` would merge into the failed one.
- **How it works.** `_close_agent_init_failed_turn` (`tui_gateway/methods_prompt.py`) writes only when `db.latest_conversation_role(sid)` is in `OPEN_TURN_TAIL_ROLES`, so it is idempotent on the durable tail like the other two closers (a redelivery, or a tail another writer already closed, is a no-op). The error goes through `redact_sensitive_text(..., force=True)` and is cut to 2,000 characters. Best-effort: a store write failure is logged as a warning and the terminal frame still goes out. A turn cancelled before the build finished (`_turn_cancel_requested`, no `agent_error`) is unchanged: no boundary, no outcome.
- **Code.** `tui_gateway/methods_prompt.py` (`_close_agent_init_failed_turn`, `_run_after_agent_ready`).
- **Tests** (in the patch; not run for this mirror). `tests/tui_gateway/test_turn_outcome_refresh.py`: `test_agent_init_failure_writes_one_boundary_and_reads_turn_failed`, `test_agent_init_failure_redelivery_adds_no_second_boundary`, `test_agent_init_failure_secret_in_error_is_redacted`, `test_cancelled_before_ready_turn_is_unchanged` (control). `tests/tui_gateway/test_submit_time_user_row.py::test_failed_build_drops_the_staged_row_and_a_later_turn_never_adopts_it` now expects the boundary between the two user rows. The four new tests call `server._run_after_agent_ready` directly with stubbed `_emit`/`_session_info`; the JSON-RPC `prompt.submit` entry and Desktop rendering of the boundary are untested end to end.
- **Gotchas.** Only the TUI gateway's deferred-build path changes; a messaging-platform gateway turn failing before its agent exists is not covered by this patch. If the session store is unwritable the outcome still reads `interrupted` (the boundary row is what makes it `turn_failed`).
