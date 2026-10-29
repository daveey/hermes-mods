# Stored chat outcome, server-side mute and sidebar stripe

Patches:

- `patches/0993-feat-sessions-stored-chat-outcome-server-side-mute-t.patch`
- `patches/0994-fix-sessions-outcome-refresh-off-the-messaging-reply.patch`
- `patches/0995-feat-sessions-outcome-refresh-on-notices-and-card-mo.patch`
- `patches/0996-feat-desktop-paint-the-sidebar-from-the-stored-sessi.patch`
- `patches/0997-feat-desktop-push-old-local-B-parks-to-the-backend-o.patch`
- `patches/0998-fix-desktop-unmute-clears-the-stale-muted-outcome-bo.patch`
- `patches/0999-refactor-desktop-bring-session-outcome-under-the-cod.patch`
- `patches/1003-fix-sessions-CLI-and-web-outcome-refresh-load-plugin.patch`

Every chat needs one shared answer to "is this done, waiting on me, or stuck?" instead of each surface re-deriving it from the transcript. This series stores that answer on the session row and paints the Desktop sidebar from it.

## Behavior

- `sessions.outcome_json` / `outcome_at` hold one outcome per chat, and `sessions.muted` is a lineage-wide mute flag. `hermes_state_outcome` derives it by contract precedence 1-8: muted, live turn, open decisions, live work, a held declaration, tail shape, summary judge/heuristic, empty. Plugins add a judge and providers.
- It refreshes at every Desktop/TUI turn conclusion, every messaging-platform turn end (on a daemon thread, off the reply path), on orphan reap, on kanban notices and card moves, and from CLI/web refresh.
- A judged outcome stores `judged=true` and the judged message id; a refresh in a process with no judge keeps that verdict for the same message. Judges are called as `fn(sid, text, source=...)`, falling back to `fn(sid, text)`.
- Notice guarantee: a chat ending on a kanban notice older than 5 min with no live turn gets one close-out wake (claimed by CAS, 2 per hour per lineage). A notice about a card blocked on a human never wakes and refreshes to decision. Earlier "blocked" notices are marked `display_metadata.stale` once the card leaves blocked. No refresh for released links, archived chats or moved copies.
- `desktop.auto_continue.resume_on_startup` defaults on.
- Surfaces: list rows carry `outcome` and `muted`; `POST /api/sessions/{id}/mute`; `GET /api/sessions/{id}/outcome?refresh=1`; `hermes sessions mute|unmute|outcome|outcome-audit`. CLI and web refresh load plugin providers and the judge first, so the audit cron cannot store `done` over a chat waiting on a user decision.
- Desktop: `sessionStatusStripe` prefers `row.outcome` (done green, decision red, muted grey, waiting/running grey glyph, unresolved amber); a live turn and a local park still win. The stripe tooltip shows the summary, open questions or the unresolved reason. The park hotkey writes through the mute endpoint, keeps the local list as an optimistic cache and migrates old local parks once per profile and gateway. Unmute drops the stale `muted` outcome; automated rows ignore stored outcomes. Stale notices render dimmed as "outdated: card moved on".

## How it works

`hermes_state_outcome.py` derives, stores and refreshes; the gateway, TUI gateway, reaper and notice paths call the refresh. `tui_gateway/notice_guarantee.py`, `notice_fanout.py` and `reap_notices.py` implement the wake and fan-out filter. Desktop code moves the stripe into `sidebar/session-row-stripe.tsx`, outcome types into `types/session-outcome.ts` and copy into `i18n/en_session_row_status.ts` (0999, no behavior change).

## Code

`hermes_state_outcome.py`, `hermes_state_sessions.py`, `gateway/turn_outcome.py`, `tui_gateway/turn_outcome.py`, `tui_gateway/notice_*.py`, `hermes_cli/sessions_cmd_outcome.py`, `hermes_cli/web_routers/sessions.py`, `apps/desktop/src/app/chat/sidebar/session-row*.tsx`, `apps/desktop/src/store/session-blocked.ts`.

## Tests

Test files added or changed by these commits (not re-run for this mirror): `tests/hermes_state/test_session_outcome.py`, `test_session_outcome_judge.py`, `tests/gateway/test_turn_outcome_refresh.py`, `tests/tui_gateway/test_turn_outcome_refresh.py`, `test_session_outcome_restarts.py`, `tests/hermes_cli/test_session_outcome_surfaces.py`, desktop `session-row.test.tsx`, `session-mute.test.ts`, `session-blocked.test.ts`, `sidebar-automated.test.ts`, `use-session-list-actions.test.tsx`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

Older backends lack `muted`/`outcome`; the Desktop keeps the local park list as the only record then. The session schema gains columns (`session_schema_history.py`).

## Plugin

The user-steps plugin supplies the decision provider; a judge plugin is optional.
