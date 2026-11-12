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
| Sidebar | Registers a `kanban_session_activity` waiting source (core seam `register_waiting_source`): a chat whose batch has settled but whose close-out has not run stays grey **waiting on N cards** (open cards are already counted by core). Never green, never blank, in between. A card blocked on the user paints the row red as before. Each pass reads the remote inventory (hub-replica projection) once for all pending rows, and is single-flight: concurrent session-list polls wait for the running pass and share its 3 s cached answer (`tests/test_waiting_source.py`). Its board read is light (settlement fields only: no per-card event/run subqueries or links; the full statement is still compiled, so a board the full read can't query is refused the same way), a board whose files are unchanged since the last read (db and -wal identity/size/mtime, db change counter, WAL salts) is not read again, and only inventory rows whose id could match a ledger key pay for `card_key` (`tests/test_light_read.py`). Ack proofs read each lineage's transcript once per pass (`core.AckIndex`: user/assistant rows only, older slices prepended as lower completion times come up) instead of once per finished card; a randomized test proves it answers exactly as the per-card scan (`tests/test_ack_index.py`). |
| Close-out brief (`pre_llm_call`, conditional) | When a turn starts after the whole batch settled (done, review, or blocked on a human), injects each card's handoff and asks for the closing answer. Only a close-out turn (briefed here, or started by the waker) marks the batch's settled cards *covered*; an ordinary turn during which a card finished covers nothing. Cards the close-out turn itself creates that are already settled (a needs_input ask) are covered too. Covered cards that are done/archived leave the batch; human-blocked ones stay covered until they move. |
| Close-out wake (backend thread, 30 s tick) | When every batch card has settled, its notices were handed to the chat (`kanban_unseen` = 0) and the user has not typed there for 5 min, wakes the chat in-process (core `server._wake_session_turn`: the live chat, or `session.resume` into this backend) for ONE close-out turn. The wake key is the id set of settled cards no close-out has reported yet, never their statuses: settled→settled churn (blocked→archived, done→archived) and cards the close-out turn creates never wake again. A covered card that goes back to work (unblocked → running) is uncovered, so its real result gets one more close-out. Hard cap: `MAX_WAKES_PER_HOUR` (2) per chat, logged as a WARNING when hit. The claim is a CAS in the ledger, so two backends never both wake it; busy releases the claim for the next tick; refused/error undoes coverage and sets a shared, durable 600 s retry deadline in the claim CAS, so multiple backends cannot retry each tick. `wake_count` in the ledger counts wakes per chat. Each tick reads the remote inventory once for all pending rows (`tests/test_waiting_source.py`) and runs in one proof pass: its ack proofs, session-source and last-user reads share one connection per state.db (`tests/test_ack_index.py`). |
| Ask relay (backend thread, same tick; `pre_llm_call` too) | A batch card that waits on **the user** (blocked/triage with a validated `human_request`: all five fields, not a dependency/transient/time wait) while other batch cards still work, typically a todo card waiting on it, so the batch never settles and no close-out runs. The chat gets ONE turn (`ask_relay_prompt`): what is stuck in plain words, the one thing the user must do and where, what happens after, and which work waits behind it; card ids are not the explanation. "Waits behind it" is claimed only for batch cards that list the ask card as a parent (`read_cards` returns each card's `parents` from `task_links`); with no such card the prompt says nothing else waits on this, that it is optional for the rest of the work, to name the default if the user does nothing, and not to restate it later (defect agent-31a66d274a: an optional key-rotation ask was relayed as a blocker and restated for 43 h; `tests/test_ask_relay.py`). Once per card per **premise** (`card@hash` of status, block kind and the ask; recorded in the ledger's `relayed` column), so a second tick adds nothing and a re-block for another reason (or the same block after the card ran again) is a new ask. Same claim CAS, 5-min user-quiet rule, unseen-notice gate and hourly cap as the close-out; busy retries next tick, refused/error waits at least `owner_wake.CLAIM_TTL_S` (600 s), across processes and restarts. An `error` from a chat whose **exact-pinned** subscription model has a known quota cooldown (core `quota_unavailable`; the wake dies at agent build and core automation replay already schedules one replay at the reset) is recorded as `quota` and waits until the reset, re-checked at most hourly (`QUOTA_RECHECK_S`) in case the user switches model; the watchdog holds `ask-failed`/`no-closeout` incidents while that wall holds, without resolving or resetting them (`tests/test_quota_wall.py`, `tests/test_quota_incident.py`). A persisted `quota` retry deadline in the future is authoritative without a second pool probe. The wake uses the build exception’s recorded `agent_failure.failure_resets_at` before the pool fallback, including the core automation-replay failure branch; directly raised `SubscriptionQuotaUnavailable` is classified the same way (`tests/test_wake_failure_quota.py`; core-backed integration tests require core). Before an ask wake, consult the Board owner for the compression root in the correct profile store. A moved chat uses one durable `session-mentions` courier handoff, not a local wake. A user turn that starts while the ask is untold gets the same brief in `pre_llm_call` and covers it at its end. A close-out records the asks it reported, so they are never relayed again, and asks covered before the relay existed are left alone (no burst on upgrade). The close-out prompt carries each card's ask and the same plain-words rule (defect agent-8cecfe01cb, `tests/test_ask_relay.py`). The visible notices (the close-out's "N waiting on you" bubble and the relay's "Needs you" bubble) list each user-blocked card's ask line verbatim, never truncated, and the prompts quote it in full; a blocked card with no valid ask says so instead of a bare title (`tests/test_ask_notice_verbatim.py`). A stored ask is not true forever: each ask line carries its age (`asked 31 h ago`, from the card's newest `blocked` event via `read_cards`' `blocked_at`), the plain-words rule makes the agent check the ask still holds (newer comments, the work's skill, whether the account/repo/page it names still exists) and, if not, comment the correction and unblock the card instead of asking, and an ask older than `ASK_RECHECK_S` (12 h) also gets an "old ask: re-verify" line. Each relayed ask logs `ask_relay aged=<h> card=<id>` for the coach (defect agent-792a49aa96, `tests/test_ask_relay.py`). |
| Owner wake (backend thread, same tick, `owner_wake.py`) | A card the chat created or shepherds (local, or on another gateway via the origin projection) that parks for an **agent-owned** reason (transient/capability/assignment block, triage after gave_up or a block loop, continuation_exhausted, a schedule with no named producer; a legacy opaque-probe park only once it has stalled 2 h) starts ONE supervise turn in that chat: what each card waits on and since when, and "clear it with this gateway's capabilities, create the owner card where the capability lives, or reassign; don't re-park on the same premise". Once per card per **premise** (core `kanban_wait_info.premise`: status + kind + reason text, never timestamps), at most `MAX_WAKES_PER_CHAT_HOUR` (3) per chat. An **archived, reaped (`startup_orphan_reap`) or missing** chat, or an **ended** kanban-worker/cron/webhook/subagent session (`end_reason` set or archived; a live one is still skipped, since its dispatcher or parent owns the card), routes the cards to the board supervisor instead: one ready card per chat on this gateway's default board, assigned to `kanban.orchestrator_profile` (else `default`), at most `MAX_SUPERVISOR_ROUTES_HOUR` (6) per hour. Fork core lets that supervisor card's worker `kanban_unblock` exactly the cards its body lists while they are in triage or blocked. A wait older than `MAX_AGE_S` (48 h) when first seen never wakes a chat (recorded `too_old`), but a supervisor route ignores age and is not held back by a `too_old` record (defect t_01a18ba39f9cb81a: four hub cards whose owners were an ended worker and a cron run sat in triage 1.5-3 days). Claims live in `<kanban home>/cache/chat-closeout/owner-wake.db`; busy/refused/error releases the claim for a later tick. **Reverse dependencies**: both the chat wake and the supervisor card name, per card, the open cards still waiting on it (`dependents_from`: local children via `task_links`, cards whose latest park/block reason or wake check names it, and any gateway's open card in the hub replica whose parents or last run summary name it) with "do NOT close or archive it while they wait", and every wake carries the rule to check reverse dependencies on every gateway before closing: an archived owning chat never makes a card safe to close. Cards on a gateway this one cannot see are left to that check (untested beyond the prompt text). Needs core `kanban_session_activity.parked_waits` (fork); older cores: no-op (`tests/test_owner_wake.py`). |
| Turn-end lint (`pre_final_gate`) | A final reply promising to report later ("I'll report back", "when it finishes", "running now") while the chat has no open working card (one it created, or one created elsewhere that notifies it through `kanban_notify_subs`, such as a worker's follow-up card or a card the chat commented on), background process or cron job is held once with a private nudge (start the tracked thing, or give the result now); a second attempt gets a visible warning line appended. Quotes and code are ignored. |
| Turn-end visibility (`pre_final_gate`) | Every card a successful `kanban_create` returned this turn (local, placed on another gateway, or through the `tool_call` bridge; keyed on tool results, not wording) must appear in the chat's own task list, core `session_tasks` over the lineage (the list behind `/api/session-kanban-tasks`; a card already done counts). A missing card is re-read once after 3 s (replica lag), then the reply is held once with a private nudge (link it and re-check, or say plainly it isn't linked); a second attempt gets `⚠ t_x isn't shown in this chat's task list yet.` appended. Logs `chat-closeout: turn-end visibility ok\|held card=<id>`. A failed read lets the reply through. `CHAT_CLOSEOUT_OFF=1` disables it with the rest of the plugin. |
| Watchdog (every 15 min, one process) | Kanban card (default board, assignee `default`, idempotency key = the incident flag key, so one card per incident) + `WARNING` log line for a chat with unseen kanban results > 30 min, a settled batch with no close-out > 30 min, or a row **stuck** for 24 h: nothing it waits on is found and still working by status (`core.is_working`: readable, not settled, not done/archived), and at least one entry is unresolved (`core.is_unresolved`: missing, unknown or stale, or done/archived yet unable to settle, e.g. an origin row without its binding or an owner that never attested the close), so no close-out can ever run (key `chat-closeout:stuck:<chat>:<first seen>`, one card per row episode; wall age >= 24 h plus 30 min of this process's monotonic observation, so a lid sleep alone never files it and a backend restart does not postpone it a day; a row whose entries are all found and settled, e.g. covered human asks, is resting, not stuck; `tests/test_stuck_watchdog.py`). Age needs both the persisted wall stamp and 30 min of this process's monotonic observation, so a lid sleep never fakes a stuck pipeline. The flag is recorded only after the card is filed; after 3 failed filings for one key it logs one `ERROR` and stops retrying until the condition changes (`tests/test_watchdog_real_card.py` runs the real default route against a temp board). When a flagged incident's condition clears (unseen count 0, `done_sig` matched / `settled_sig` actually changed / ledger row or chat gone, a stuck row with a found working entry or an emptied batch, a handoff delivery receipt, or the ask failure episode changed), the next tick closes its own card: unblocks it if blocked, claims and completes it with a summary naming the key, the first observed-clear time (persisted across restarts) and the evidence; a card with a live run gets one comment and is retried next tick. Then the flag is cleared, so a recurrence files a new card. Card text names agent routes (cross_session send, the close-out waker), never "resume the chat" (`tests/test_watchdog_resolution.py`). Agent-fixable state never goes to the Inbox, which refuses poster `chat-closeout`. Each tick reads the remote inventory once for all pending rows (`tests/test_waiting_source.py`). A **deleted** chat (a Hermes-format chat id that every state.db reads cleanly without; e.g. a worker's throwaway live-test chat) is never flagged: its notices can never be handed over, so the waker drops its ledger row and the watchdog skips its unseen/no-close-out conditions. An unreadable store or a non-local id is unknown, never deleted (`tests/test_deleted_chat.py`, `tests/test_deleted_chat_watchdog.py`). |

## Self-relayed asks

Before either the background wake decision or `pre_llm_call` brief, a successful final assistant reply
in the chat lineage that contains the normalized current action after `blocked_at` records that premise
as relayed and the card as covered. This includes retained delivered replies after compaction, not synthetic
summaries (which lack a successful finish reason). User-role injected prompts and tool results never count.
Missing timestamps or unreadable transcripts preserve the ordinary relay. Filing actor profiles alone do
not establish which session filed a block.

`tests/test_self_relay.py` covers both entry paths, settled and working batches, prior coverage,
fresh/stale/absent quotes, normalization, injected notices, failed replies and other sessions.

Every hook fails open. Skips kanban workers, cron, webhooks and subagents. `CHAT_CLOSEOUT_OFF=1` disables it.

Needs the fork core for the wake (`tui_gateway.server._wake_session_turn`) and the sidebar seam
(`kanban_session_activity.register_waiting_source`); on an older core the ledger, brief and lint still work, the
row goes green a little early, and no wake happens.

## Historical completion receipts

Full local-board inventory reads include core's canonical completed-event/run receipt
projection when available. The source gateway comes from the task's original authority,
not whichever worker currently holds a synced row. Portable run/event identities and
original evidence observation/verification travel unchanged into the closeout prompt;
a newer review handoff cannot replace the completion receipt. Light settlement reads
are unchanged. Missing or invalid receipts leave artifact acceptance unspecified.

Receipt-bearing closeouts distinguish rejected artifacts, superseded scope, delivered
reports and containment (not a root-cause fix). They preserve remaining scope/qualified
owners and do not turn explicit deferral into execution, publication or retry authority.
Receipt references remain historical, including when current execution evidence is stale.
The receipt is never a substitute for the existing owner-attestation/settlement gate.
Task-run provenance is validated by core before rendering or budget fallback: only
bounded source fields and relocations of actual evidence/superseded references are
accepted. Invalid provenance leaves artifact acceptance unspecified, not an arbitrary
JSON/UID fallback. Prompt-budget omissions name the qualified task ref, not raw UIDs.
`tests/test_completion_receipt.py` exercises the read projection and generated prompt,
including legacy absence, all dispositions, malformed/oversized provenance and prompt
bounds. `tests/test_remote_completion_receipt_closeout.py` exercises real remote
projection through brief/wake delivery. Live Desktop delivery is not proved here.

## Ownership and evidence

New batches use local `board/task` keys and remote `gateway/profile/board/task` keys (the local gateway/profile is implicit in the ledger's runtime scope). Remote reads use core's origin-ledger projection, including released rows omitted from `session_tasks`, plus core's unsuperseded historical associations. There is no second ownership ledger or replica-only completion rule.

A remote done/archive settles only with fresh owner evidence: an origin row needs its binding (`binding_id`) and `unfinished` False; a shepherd row (core `_read_remote_links`, `via='shepherd'`) needs `stale` False and `unfinished` False, which core sets only when the owner attested the close on a fresh replica. A stale or unfinished shepherd row, and an origin row without a binding, never settle; a covered shepherd card that goes back to work is uncovered (`tests/test_shepherd.py`, table-driven over local statuses, origin bound/unbound, shepherd fresh/stale/unfinished and missing). A finished shepherd row's transcript acknowledgement uses its `completed_at`, taken from another inventory row of the same owner key or, failing that, the hub replica's task record. Unknown, stale, missing and review remain unfinished; current explicit `needs_input`/`capability` blockers still produce the human ask. Local settlement behavior is unchanged. Historical bare-ID batches resolve only when unambiguous: collisions or an unreadable remote inventory remain unknown rather than borrowing local completion. Such ambiguous historical entries require explicit reconciliation, not an automatic ownership guess. Remote projection currently has no handoff summary, so the brief says `(no handoff)` rather than substituting a local result.

**Upgrade.** Shepherd rows could not settle before this release, so ledgers hold shepherd cards that finished long
ago. The first pass after the upgrade (run first by every path that can wake or brief a chat: the waker, `pre_llm_call`,
`post_llm_call`, the sidebar source and the watchdog) removes those finished entries silently, as a close-out would,
logging one INFO line per changed row with its card ids and waking nothing. The first pass records a durable cutoff
(`meta upgrade:shepherd-settle-v1:cutoff`) and a snapshot of every batch entry then in the ledger (`...:pending`).
Only a snapshot entry is ever covered this way, initially when its first fresh observation shows a finished shepherd
row whose completion time is unknown or not after the cutoff. That first observation resolves the entry either way:
a card seen working at deploy, a finish after the cutoff, a local card, or an entry absent from a readable inventory
takes the normal path for good, and entries added after the cutoff are never touched, so they close out normally
even while an old stale row keeps the upgrade incomplete. The done marker (`meta upgrade:shepherd-settle-v1`) is set
once every snapshot entry was observed or left its batch (`tests/test_upgrade_shepherd.py`,
`tests/test_contract_edges.py`). When retiring shepherd entries would expose an entirely old, already-settled
local/origin tail, that snapshot tail is covered silently too; otherwise it would cause the same upgrade wake burst.
Post-cutoff completions and unresolved origin rows are never covered this way (`tests/test_upgrade_mixed.py`).

Legacy duplicate keys are reconciled only against a found sibling in the same batch: an absent bare `id`
is dropped when a found key ends in `/id`, and absent `gateway/profile/id` is dropped when a found
`gateway/profile/board/id` exists (`tests/test_aliases.py`). A legacy bare `id` or 3-part `gateway/profile/id`
with no local match that matches exactly one inventory owner key by card id is rewritten to that key in the batch
and the coverage (and relayed asks); two candidate owners, or a local card with that id on any board, leave it
untouched. The read looks every ledger key's card id up as a bare id on every local board for this.

**Unlinking.** An entry that matches nothing (no local card with its card id on any board, no inventory row with
its card id under any owner key, and not merely unresolved: a local collision, a failed board read or several owners
keep it) leaves the batch after 24 h of observation while the
inventory is fresh and readable: the projection was read, no replica row is stale, and the hub replica synced within
core's `HUB_STALE_AFTER_S`. Unreadable or stale periods are excluded but do not reset what was already observed;
each observation counts only as far as this process's monotonic clock confirms the wall clock, and nothing across a
gap over 5 min or an observer's first pass. Each backend retains its own clock anchor while sharing the last
observation time, so alternating backends make progress without double-counting (`tests/test_upgrade_mixed.py`).
A pass that cannot resolve the entry (uncertain: unreadable inventory, failed
board read) is not observation either: it ends the interval, so even an outage shorter than 5 min never counts. The ledger's `unlinked` column keeps the first missing time and observed seconds
per entry. The next close-out names each unlinked entry once ("no longer linked to this chat"). An unlinked entry is part
of the close-out key, so a batch that unlinking empties (or leaves holding only covered human asks) still gets that
one close-out; a batch with cards still working waits for its own. The mention is forgotten only at the end of a
close-out turn whose last assistant message is a successful final reply (text, no tool calls, `finish_reason`
stop/end_turn, the transcript ack rule); a failed wake, or a briefed turn that failed, keeps it, and a failed turn
retries after a shared 600 s backoff subject to the existing hourly wake cap; failure never acknowledges delivery. A found entry
forgets its missing time (`tests/test_unlink.py`, `tests/test_contract_edges.py`).

A missing four-part `gateway/profile/board/id` additionally requires a fresh full Board shadow
(`kanban/sync-shadow/<board>.db`, matching board identity and `pulled_at` within 120 seconds).
The origin/shepherd projection only contains linked rows; freshness there does not establish full-board absence.
Any task with that id in the shadow vetoes unlinking, regardless of status or owner, without granting settlement.
A missing, unreadable, wrong-board, future-dated or stale shadow is uncertain, not an observed absence. These
vetoes discard previously accumulated missing time on the first pass. Hosts without full-board coverage retain
unresolved qualified entries rather than guessing. An unresolved legacy three-part key is also uncertain:
its middle part historically meant either profile or board, so no single board can prove absence until an
inventory row supplies a canonical owner. An unresolved bare id names no board, so it is checked against every
board this host knows of beyond its local boards: each `sync-shadow/*.db` and each link-only `sync-links/*.db`.
The id in any of them (fresh or stale), or any of them missing, unreadable, wrong-board, future-dated or stale,
makes the pass uncertain and discards earlier missing time; only a card absent from all of them (all fresh) is
observed absent. A host with neither directory keeps the local-boards-plus-projection rule
(`tests/test_bare_coverage.py`). This escaped `test_board_coverage.py`, which seeded only 3- and 4-part keys, and
the live check, which filtered timers by `/runners/` instead of by card id.
The Board shadow is shared across gateways/profiles (not a gateway partition); its `gateway_id` identifies the
reader, not the owner of every task. This is why coverage is checked by board and presence by id.
A key that no local board or projection row resolves but a FRESH shadow holds with a readable status is read as
that card (`via: shadow`, remote, `stale` False, the shadow's status/`completed_at`/title/block): typically a hub
card a hub session created, never origin-bound to this chat. Done/archived settles it (a close-out unless the
transcript already reported it), anything else is working (or an ask). Before this such keys stayed uncertain
forever: never aged, never settled, and the watchdog filed "waits on cards nothing can find" for them. A four-part
key resolves from the board it names; a bare / three-part key only when every known board is fresh and exactly one
holds the id, and then a resolved four-part sibling of the same id and board becomes its `canonical_key` so
`reconcile` collapses it. A key naming a gateway resolves only from a row that gateway created (`created_on`):
another owner's card of that id still only vetoes. Stale, missing, unreadable or status-less coverage, keys the read
already found ambiguous, and a pass whose projection failed never resolve (`tests/test_shadow_resolve.py`).
`tests/test_board_coverage.py` exercises full/light readers, first-pass reset, projection failure, >24-hour
retention and genuinely absent full-board deletion. The defect escaped the earlier list-only tests because those
fixtures assumed the linked projection was exhaustive; deletion fixtures now supply full-board evidence.
The regression was retained across upgrades because turn-end unions prior batches while readers use canonical
keys; coverage upkeep lacked alias migration. Tests now seed mixed historical/canonical batches and exercise
wake plus turn-end, including open canonical and missing-sibling controls.

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
Registration logs `shepherd-settle-v7` and the selected module paths to distinguish activation from files on disk.

## Moved asks and failed delivery

`routing.py` reuses the existing session-mentions courier; it does not create another transport.
The Board owner remains authoritative when a moved-away gateway has switched its local board mode off.
Unknown ownership during a Board outage preserves core's fail-open admission; a previously known moved
owner remains fenced using the last cached answer. Named profiles use their own compression lineage and
target profile (`test_relay_owner.py`).

The handoff intent is committed to the shared ledger before the external enqueue. The deterministic
request ID and committed intent mean no second send even after a crash, lost acknowledgement, restart,
or disappearance of courier files (`test_relay_routing.py`). `forwarded` means handed to the courier,
not delivered: receipts are copied into the ledger, queued/streaming are target acceptance, and an
unknown/refused/missing receipt remains unconfirmed. Do not automatically replay an uncertain send.
The watchdog files an agent-owned incident for failed asks even while other batch cards work, and for
unconfirmed handoffs after 30 min of wall time AND process observation (`test_relay_watchdog.py`).
This moved-owner preflight is for ask relays; settled close-out owner routing is unchanged. Shared
refusal backoff limits attempts, not a guarantee that a non-owning backend yields to the owner.

Failure chain: ask relay reused local-only wake -> moved/other-owner refusal -> coverage and claim
immediately reset -> each backend tried on its next tick indefinitely. Detection missed this because
the watchdog only watched unseen results or fully settled batches, not a failed ask in a working batch.
The original ask tests exercised successful delivery and busy undo/retry, not ownership refusal or moves.
Countermeasures: owner-aware one-shot handoff, shared retry admission, receipt retention, and failed-ask
watchdog regression tests. Existing asks already reported elsewhere require evidence-backed acknowledgement,
not an automatic resend on upgrade.

## Tests

Unit tests use throwaway SQLite files. Core integration additionally exercises real owner observation, receipt acceptance and projection using isolated owner/replica/origin databases; set `HERMES_CORE_SOURCE` to the fork checkout to enable those cases. Keep worker safety environment pins intact.

    HERMES_HOME="$PWD/test-home" HERMES_CORE_SOURCE=/path/to/hermes-agent uv run --with pytest --with pyyaml --with ruamel.yaml python -m pytest -q -p no:cacheprovider plugins/chat-closeout/tests

## Dropped resolved_check

An ask whose `human_request` carries `resolved_check_dropped_at` (core stopped probing it) is relayed with a leading "Automatic detection stopped ... needs a manual unblock until re-filed" warning in the ask notice, close-out notice and both prompts; the filed text follows labelled historical. The ask premise changes, so it is relayed once more. Older cores never set the keys. Tests: `tests/test_dropped_resolved_check.py`.
