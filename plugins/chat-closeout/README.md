# chat-closeout

A chat that hands its work to Kanban cards always shows what it waits on, and closes itself out when they finish.

## Why

A coordinator chat handed all of its work to cards and ended its turn. Nothing recorded "this chat waits on t_x,
t_y", so the sidebar row went green (looked finished) and, when the cards finished days later, nobody wrote the
closing answer. Core already delivers completion notices into unloaded chats (the unloaded-session sweeper,
`kanban_unseen`). This plugin closes the behaviour gap in code instead of relying on the agent remembering to.

## What it does

| Piece | Behavior |
|---|---|
| Waiting ledger (`post_llm_call`) | Every turn end stamps the chat's open cards (created or shepherded, not done/archived) into a per-chat **batch** in `<kanban home>/cache/chat-closeout/ledger.db`. Cards stay in the batch across compression until it is closed out. |
| Sidebar | Registers a `kanban_session_activity` waiting source (core seam `register_waiting_source`): a chat whose batch has settled but whose close-out has not run stays grey **waiting on N cards** (open cards are already counted by core). Never green, never blank, in between. A card blocked on the user paints the row red as before. Each pass reads the remote inventory (hub-replica projection) once for all pending rows, and is single-flight: concurrent session-list polls wait for the running pass and share its 3 s cached answer (`tests/test_waiting_source.py`). |
| Close-out brief (`pre_llm_call`, conditional) | When a turn starts after the whole batch settled (done, review, or blocked on a human), injects each card's handoff and asks for the closing answer. Only a close-out turn (briefed here, or started by the waker) marks the batch's settled cards *covered*; an ordinary turn during which a card finished covers nothing. Cards the close-out turn itself creates that are already settled (a needs_input ask) are covered too. Covered cards that are done/archived leave the batch; human-blocked ones stay covered until they move. |
| Close-out wake (backend thread, 30 s tick) | When every batch card has settled, its notices were handed to the chat (`kanban_unseen` = 0) and the user has not typed there for 5 min, wakes the chat in-process (core `server._wake_session_turn`: the live chat, or `session.resume` into this backend) for ONE close-out turn. The wake key is the id set of settled cards no close-out has reported yet, never their statuses: settled→settled churn (blocked→archived, done→archived) and cards the close-out turn creates never wake again. A covered card that goes back to work (unblocked → running) is uncovered, so its real result gets one more close-out. Hard cap: `MAX_WAKES_PER_HOUR` (2) per chat, logged as a WARNING when hit. The claim is a CAS in the ledger, so two backends never both wake it; busy / refused / error releases the claim and undoes the coverage for a later tick. `wake_count` in the ledger counts wakes per chat. Each tick reads the remote inventory once for all pending rows (`tests/test_waiting_source.py`). |
| Owner wake (backend thread, same tick, `owner_wake.py`) | A card the chat created or shepherds (local, or on another gateway via the origin projection) that parks for an **agent-owned** reason (transient/capability/assignment block, triage after gave_up or a block loop, continuation_exhausted, a schedule with no named producer; a legacy opaque-probe park only once it has stalled 2 h) starts ONE supervise turn in that chat: what each card waits on and since when, and "clear it with this gateway's capabilities, create the owner card where the capability lives, or reassign; don't re-park on the same premise". Once per card per **premise** (core `kanban_wait_info.premise`: status + kind + reason text, never timestamps), at most `MAX_WAKES_PER_CHAT_HOUR` (3) per chat. An **archived, reaped (`startup_orphan_reap`) or missing** chat routes the cards to the board supervisor instead: one ready card per chat on this gateway's default board, assigned to `kanban.orchestrator_profile` (else `default`), at most `MAX_SUPERVISOR_ROUTES_HOUR` (6) per hour. Claims live in `<kanban home>/cache/chat-closeout/owner-wake.db`; busy/refused/error releases the claim for a later tick. Needs core `kanban_session_activity.parked_waits` (fork); older cores: no-op (`tests/test_owner_wake.py`). |
| Turn-end lint (`pre_final_gate`) | A final reply promising to report later ("I'll report back", "when it finishes", "running now") while the chat has no open working card, background process or cron job is held once with a private nudge (start the tracked thing, or give the result now); a second attempt gets a visible warning line appended. Quotes and code are ignored. |
| Watchdog (every 15 min, one process) | Inbox item + `WARNING` log line for a chat with unseen kanban results > 30 min, or a settled batch with no close-out > 30 min. Age needs both the persisted wall stamp and 30 min of this process's monotonic observation, so a lid sleep never fakes a stuck pipeline. The flag is recorded only after the Inbox post succeeds. Each tick reads the remote inventory once for all pending rows (`tests/test_waiting_source.py`). |

Every hook fails open. Skips kanban workers, cron, webhooks and subagents. `CHAT_CLOSEOUT_OFF=1` disables it.

Needs the fork core for the wake (`tui_gateway.server._wake_session_turn`) and the sidebar seam
(`kanban_session_activity.register_waiting_source`); on an older core the ledger, brief and lint still work, the
row goes green a little early, and no wake happens.

## Ownership and evidence

New batches use local `board/task` keys and remote `gateway/profile/board/task` keys (the local gateway/profile is implicit in the ledger's runtime scope). Remote reads use core's origin-ledger projection, including released rows omitted from `session_tasks`, plus core's unsuperseded historical associations. There is no second ownership ledger or replica-only completion rule.

A remote done/archive settles only with a fresh, verified origin release. Unknown, stale, missing and review remain unfinished; current explicit `needs_input`/`capability` blockers still produce the human ask. Local settlement behavior is unchanged. Historical bare-ID batches resolve only when unambiguous: collisions or an unreadable remote inventory remain unknown rather than borrowing local completion. Such ambiguous historical entries require explicit reconciliation, not an automatic ownership guess. Remote projection currently has no handoff summary, so the brief says `(no handoff)` rather than substituting a local result.

## One pending decision

`core.pending_key` is the single "does this row need a close-out" answer used by the sidebar source, `pre_llm_call`,
the waker and `closeout_due`. `core.reconcile_done` settles rows whose `done_sig` would otherwise hide a card forever:
done before the stamped close-out, or notice delivered and answered after completion (`ack_proven` over state.db) ->
covered; completed after the stamp with no proof -> `done_sig` cleared so the final handoff is closed out. A card
reopened during a close-out turn no longer gets its stale `settled_sig` stamped as `done_sig`.

Historical acknowledgement requires trusted settled state even when a transcript proof callback succeeds.
Notices match the exact board/task or full remote owner key, never a bare-ID substring. Ambiguous legacy remote
notices fail closed. Only active successful final replies (`stop`/`end_turn`, no tool calls, no commentary or failed
turn display kind) count; missing completion metadata and interrupted replies remain unacknowledged.
Registration logs `owner-final-proof-v2` and the selected module paths to distinguish activation from files on disk.

## Tests

Unit tests use throwaway SQLite files. Core integration additionally exercises real owner observation, receipt acceptance and projection using isolated owner/replica/origin databases; set `HERMES_CORE_SOURCE` to the fork checkout to enable those cases. Keep worker safety environment pins intact.

    HERMES_HOME="$PWD/test-home" HERMES_CORE_SOURCE=/path/to/hermes-agent uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider plugins/chat-closeout/tests
