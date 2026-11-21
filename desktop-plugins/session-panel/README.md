# Session Panel

The panel displays the current chat's tasks and decisions using the owner-qualified session-tasks and user-steps projections.

## Hub model renderer (P2, opt-in)

Settings → Plugins → Session Panel exposes **Use hub-computed status panel**.
The Desktop-local boolean `sessionPanel.hubModel` defaults to **off**; switching it
off immediately restores the legacy panel below. No shared-config or default flip
is part of P2.

When enabled, `renderModel(model)` paints the pushed PanelModel v1 header and six
sections without classifying cards, parsing briefs, or reading extra data. Existing
feed registration/reconnect remains; models cache by connection/profile/stored chat
(up to 100) and paint synchronously on a switch. The legacy panel component and
its warming reads are not mounted/run in model mode. Missing models show an explicit
unavailable message, never an unrelated chat's snapshot.

Question choices use the existing user-steps decisions POST with `choice_id`, the
exact owner/session/revision and a persisted idempotency key. The owner is verified
on click before posting; delivery is read back separately. An unknown POST outcome
locks all options and offers **Check status**, retaining the key across reloads.
Card asks with no canonical request only draft composer text (nothing is sent);
P3 removes that transitional branch after widening the mirror. Open card uses the
existing qualified owner connection mapping. Details are plain text, not executable
HTML or commands.

`test/hub-model.test.mjs` calls the real Python reducer for public structural
fixtures. Set `HERMES_PANEL_GOLDENS=/private/path/private-golden-cases.json` to render
all seven preserved P1 real-data goldens too; never commit those private dumps.
Schema: [`../../plugins/session-panel/MODEL.md`](../../plugins/session-panel/MODEL.md).
Native MacBook activation/screenshots and the default flip remain P3/P4 work.

## Blocked cards and retired decisions

A canonical decision can be dismissed, withdrawn, or resolved while its owner card still reports `blocked`. Deduplicating the decision must not make that card undiscoverable.

- A request represented in the decision queue continues to own its presentation; no duplicate ask is added.
- Otherwise, a fresh blocked card appears under **Waiting**, with its gateway/profile/board/card identity, current state, and previous request marked **reference only**.
- **Open card** uses the original qualified chip. This status row never submits a decision, drafts a message, or reopens a retired ask.
- Stale rows remain status-unknown; terminal and released cards are not resurrected.
- Queue membership and panel visibility use the same predicate, including pending receipt readback. Receipt-only work keeps the queue mounted even when no actionable asks remain.

The bridge additions `presentationForSource(source)` and `hasQueuePresentation()` are optional for compatibility with older user-steps installations. They are scoped to the focused connection/profile/session; matching uses existing exact-source provenance rules.

## Continuous CI/deploy wait clocks

CI/deploy rows use `task.wait_episode` from the owner's core task-detail API. Core derives the start from durable park history: repeat wake/claim/repark cycles of the exact same producer (kind, ref, gateway) retain the first timestamp; a different producer/wait or explicit lifecycle exit resets it. History is bounded; a missing start, old backend, invalid/future timestamp or truncated beginning shows `waiting (start unknown)`, never card age, newest park age or zero. Cached reads and reloads retain the timestamp and recalculate elapsed duration with the existing render clock and `shortDur`. These rows have no disconnected trailing age.

`next check` is the armed probe time, falling back to the park's wake/backstop when no probe is available; it is not an expected CI/deploy completion time. Other clock/card waits keep their existing presentation. `test/wait-elapsed.test.mjs` covers formatting/unknown timestamps and detail reloads; `test/wait-elapsed-dom.test.mjs` mounts the real WaitLine and exercises cached clock advancement/remount. Producer episode reset and repeated scheduling are tested in core's `tests/hermes_cli/test_kanban_wait_episode.py`. No new runtime dependency.

## Inherited human requests

A session may be red because one of its waiting cards depends on a valid human ask owned elsewhere. The panel now searches **all** open parent branches, not only the first displayed chain. Fresh validated blocked ancestors appear under **Needs you · upstream dependency**, with the original action and scope (including the device named by its owner), the dependency path and qualified read source. **Open original card** opens that ancestor, not the dependent card. There are no answer, skip, permission-grant or draft controls on inherited rows.

The existing inherited-human stripe semantics are unchanged. Resolving the original ask removes its inherited row on the next current read; nonhuman waits never create one. A request already represented in the canonical queue or a directly listed card is not duplicated. Restored/pending/stale reads, legacy summaries missing graph fields, and truncated graph walks remain explicitly incomplete, never evidence of no ask. Walks are bounded to six parent hops and 80 reads per render and share the normal card cache/feed.

`test/inherited-asks.test.mjs` covers sibling branches, validation, resolution, terminal ancestors, route separation, cycle/depth/read bounds and persisted-read freshness. The idle-chat regression in `test/panel.test.mjs` exercises the real Panel and API-read cache, renders the action/path, opens the qualified original card, deduplicates the canonical queue, and removes human attention after resolution. The same test on the prior plugin fails because no upstream action is visible. Native production proof is separate from these fixtures.

## Finished history

Open cards remain visible by default. Done and archived cards share a collapsed **N finished** button; expanding it mounts their rows. Expansion is held only in renderer state, keyed by connection/profile/chat, and resets on remount. A finished-only chat retains the disclosure. Existing Done badges still count only done cards; needs-you and live agent projections are unchanged.

A failed `/session-cards` read keeps the chat's last-known Done list (the count does not vanish while the owner is busy; with nothing known, Done stays unknown); an answer that does not say it lists finished cards leaves Done unknown. The display-only `/session-cards` feed includes local done/archived cards and settled remote receipt history from the core's gateway/profile-scoped origin projection. Unknown or unverified remote obligations remain live, never reclassified from `reported_status`. Older cores without receipt history retain their existing remote-history limitation.

## Closed remote cards the owner never confirmed

A card on another gateway can report `done` or `archived` while this chat's link to it is never released (the owner confirms a completion on a later pass; a retired worker never does). session-tasks keeps such a card's state `unknown` and carries the owner's report as `reported`. The panel lists these under a collapsed **N reported finished, not confirmed** button, each row reading `reported done by <gateway> · not confirmed by its owner`, and adds `N reported finished, unconfirmed` to the header. They are not under **Waiting**, not counted as done or as evidence unknown, and never ask; they still keep the panel shown. A cached closed report survives a failed read in this same group, explicitly labelled `last known` and `the card could not be re-read`; a failed read is not evidence of reopened work. A card with no terminal report stays status-unknown under **Waiting**.

`test/panel.test.mjs` covers the classification (done, archived, needs-you look, stale, no report) and the rendered collapsed group; `../session-tasks/test/projections.test.mjs` covers `reported` for session-task rows (`/api/session-kanban-tasks`). Untested: a running Desktop against a live hub. hub-sync session-links rows carry no `unfinished` field, so they never set `reported`. Nothing here changes the core's count of the chat's open obligations.

## Board status versus execution evidence

A successful session-task read can report a parked card's board status even when it has no current execution generation. The panel labels this **board status** (or **reported board status** for a remote replica) separately from execution evidence. This presentation expires after 60 seconds without a successful read; a failed/stale read is immediately unknown. It never manufactures a run ID, heartbeat, progress, completion receipt, or a running-worker verdict.

Needs-you rows show the card's plain title alongside—not instead of—the structured action. Decision authorization and comment routing are unchanged.

`test/evidence.test.mjs` composes session-task chips with the actual SDK projector and rendered panel, including a qualified `hub/default/default/card:t_x` with no run ID, expiry and stale-read controls, and title/action context. `../user-steps/test/kanban-question.test.mjs` covers embedded title context.

## Tests

`test/composed.test.mjs` exercises the real session-panel, user-steps, and session-tasks projections together. It covers scheduled relays with blocked/nonblocked remote owners, retired and skipped asks, qualified card opening, and a terminal decision awaiting receipt readback without other open work.

Run with a Desktop dependency installation (React, React DOM, jsdom):

```sh
HERMES_DESKTOP_DIR=/path/to/desktop node --test desktop-plugins/session-panel/test/*.test.mjs
```

Historical incidents require live or preserved renderer state for exact attribution; these regressions establish the visibility failure and its correction, not the cause of every previously red chat.

## Waiting: each card's dependency chain

[daveey 2026-11-20] Every waiting card shows its full dependency chain, ending on what actually blocks it. Design: claude.ai/artifact/H13j1JAueqaFVHyzF5hpLe. A chain ends on one leaf:

| leaf | colour | when |
|---|---|---|
| you | red | a card blocked on a person: a valid `human_request` (core's `needs_human`: action, why_human, interaction, scope, evidence), a park awaiting a person, untimed with a person block kind, or a retired ask whose card is still blocked; shows the ask (wrapped, never cut) |
| external | pink | a park on a CI run, a deploy, an external/resource producer, a probe, or a timer; shows time left or how late |
| spend hold | amber | [daveey 2026-11-21] "worker slot is not it, show the real reason". The card's spend budget stopped it (`spend_budget_triaged`, newest of the stop/`promoted_manual`/`archived` marks in core's `_event_order`, as `kanban_spend_decision.pending`). No worker, unblock or triage sweep releases it: its chat must re-scope and `kanban promote` it, or archive it. Shows what was used (runs or cache-read tokens, cost). When the decision notice names a supervisor card instead of chats, the chain continues to that card |
| worker slot | grey | ready with no worker free (its `queue_note`/`capacity_note`), triage nobody works (and no spend stop), a review with no reviewer running, a todo not yet promoted, an agent-owned block (an agent kind, or a needs_input/capability block without a valid ask) |
| running | blue | the chain reaches work in progress (a running card, a running review) |
| finished | green | the blocker is done: the waiting card should resume |
| unknown | dashed | a card could not be read, a cycle, more than six cards, a park with no wake, a stale row: never a guess |

The walk (`chainFor`) starts from the waiting card itself (`firstHop`): a card its row names, else a read of the card (the same rules as every hop), else only what its row plainly states by core's wait labels (`rowLeafKind`: agent work is a worker slot, a person is you, a named producer (CI, deploy, external or resource ref, the clock, a probe) is external, `<kind> with no named producer` is a worker slot, a parent that was not read or anything unrecognised is unknown, never a guessed colour). It then reads each card it passes through once from the owner route (`/api/plugins/kanban/tasks/<id>`, the waiting card's board then `default`; cached for a minute, a failed read retried after two; a failed re-read keeps the last answer marked `not re-read`, on the card or, for the waiting card itself, on its line; the park reader and the chain share each card read) and takes its next step (`nextHop`): a dependency's or todo's first open parent, a scheduled card's park (`parkFromDetail`, as core's `park_state`), or a leaf. A card of this chat is not read: a waiting one continues from its own row, any other ends the chain by its state. A chain still being read shows `reading…` and is not counted yet.

Folded (the default), the **Waiting** heading counts the cards (and retired asks still blocked) by root cause (`blocked on ● 1 you · ● 2 external · ● 1 worker slot`) and each card has one line: its box, its title, `● <leaf> · <what> · via N cards`, and how long it has waited (first three, then `+N more waiting`); the collapsed panel keeps the same lines. Opened, each card shows its chain: the card's line names the root cause and its distance (`waiting on you · 2 hops`); each card it passes through sits on a tree guide with its state box, its title (opens it), where it lives and its state; the tinted leaf ends it. Where a card lives: `this chat`, a chip naming the chat that created it (`task.session_id`, titled from Desktop's session rows or `/api/sessions/<id>`), which opens that chat, or the owner gateway; a `local-…` session id is not a chat. A card another gateway owns (a replica read names its `owner`) is titled and opened through that gateway's connection (session-tasks' hub-name map); with no connection for it the chat is named but not opened. A card on a board other than the waiting card's or `default` ends the chain as unknown. A chain that runs into another waiting card shown above stops with `same chain as “<title>” above`. The card's title opens its long form (the full wait text, id · state · where, evidence, Open card). A blocked-upstream card is a waiting card like any other (its chain shows the blocked parent and that parent's cause), so pink means external only.

The short reason (`rowWait`), built from the card itself, never from the long text:

| the card | line |
|---|---|
| waits on another card | `on <its title> · <status>`: the title linked, cut to 28 characters (whole title, id and status in its tooltip); the status is the one the wait names, else the card's known state in this chat |
| a todo with an upstream | the same, for the upstream card |
| contained | `released by <card>` |
| blocked upstream | `parent blocked: <card>` |
| a clock wait | `10h left · Sat 00:00 PST` (Pacific time; `Dec 2 22:20 PST` beyond six days) |
| a clock wake past due | `10m overdue · due Fri 13:50 PST`; from 30 minutes late `⚠ 4h overdue`, orange |
| a CI run / PR / deploy wait | `CI · waiting 12m · run 44819160264` / `Deploy · waiting 4m · PR #7`, plus a separately labelled `next check in 2m` (URLs shortened) |
| triage | `needs triage` |
| review with no review running | `no reviewer running · <newest event, in the task rows' words> 15m ago` (the review is a run on the card itself, not a separate card) |
| scheduled with no wait on its row | its owner's real wake, read once from the card (`/api/plugins/kanban/tasks/<id>` on the owner route) the way core's `park_state` reads it: the newest `continued`/`scheduled` park unless spent (a later `continuation_woken`, or a claim/spawn of another run: then `new park not synced yet · backstop <1 h after the last event>`), its wake_at else its `SCHEDULED_UNTIL=` marker else 24 h after the park, the armed wake check (not an ask's), and a human block kind with no timed wake (`no timed wake · will be blocked for a person`). Lines: `on CI: local suite PID 7 · by Fri 14:57 PST`, `on deploy PR #3824`, `on <card>`, `on probe probe_quiet.py`, `resumes in 10m · Fri 14:57 PST`, `12d left · Dec 2 22:20 PST`; `⚠ 3h overdue` (orange) once well past its wake. `reading wake…` while the read runs (cached: re-read after a minute, a failed read after two). A failed re-read keeps the last park and adds `· not re-read`. `no wake published` (yellow) only when the owner has no park event and no check, or cannot be read. A blocked card is never read for a park (core wakes only scheduled cards) |
| blocked, no reason | `blocked · no reason given`, yellow |
| status could not be read | `status unknown · <cause>` (e.g. `source expired`); the last known state is in the long form |
| a retired ask's still-blocked owner | its status then its id, orange (`blocked · request dismissed · t_x`), never linked; while Waiting is folded it keeps its full status row, opened it lists in order, and its long form has Open card |

A ready card goes to **Ready** (`ready · waiting for a worker to claim it`) and a todo with no upstream or wait to **Not started**: neither is waiting on anything. A parked card has no run, so its unknown execution evidence is expected and is not counted as `evidence unknown` in the header; running and status-unknown rows still count.

Card titles come from this chat's own cards, else one read of `/api/plugins/kanban/tasks/<id>` on the waiting card's owner route, the waiting card's board first, then `default`. Reads are cached: a failed read retries after 5 minutes, a read that never answered after 30 seconds, and a fetched title is re-read after 30 minutes; at most 500 titles are kept. Until a read answers the line shows the bare id. A card named on another gateway (`t_x on worker-a1`), or one on a replica row of another gateway (no owner connection; its title is read through the chat's own route), is titled but not linked, as session-tasks also leaves such cards unclickable. In the long form, a wait reads `waiting on card "Title" (t_x, running)` with the title linked, and a clock wait `waiting 2h · 8h 10m left (until 2026-11-23 00:00 PST)`.

## Running rows are one line

A running card, sub-agent, process or rented machine is one line: its state box, the title (truncated), and on the right where it runs and for how long (`hub · 14 min`, `worker-1 · 2 h`, `pool · 3 h`, `in review · 2 h`, `sub-agent · 14 tools`). A running card with no event for 20 minutes reads `quiet N min` in the waiting colour (Stuck still takes over at 2 h). The tooltip says the same in words (`running on hub · for 14 min · last event heartbeat just now`).

Clicking the row opens its details: card id, assignee, board and gateway; start time and last event; the full execution-evidence line; and **Open card**. A row reaches In progress only with running evidence, so nothing hidden by default changes what the user would do; unknown evidence still lists under Waiting and counts as `evidence unknown` in the header.

The **In progress** heading shows only when another section shares the body; alone, the header's `N running` and blue boxes already name it.

`test/panel.test.mjs` covers the one-line row (the card from the 2026-11-20 screenshot), the click-open details and Open card, quiet/pool/worker/review/sub-agent meta; `test/evidence.test.mjs` covers evidence moving into the details.

## Switching chats is instant

[daveey 2026-11-20] "Session changes must be instant." A switch paints the chat's final-looking panel from caches in the first frame, then fresh reads replace it in place.

- **First frame.** Until session-tasks repaints for the new chat, the panel reads `window.__sessionTasks.cachedSnapshot(scope, stored)` (its per-chat localStorage cache), so the first frame is never empty.
- **Cached cards are judged as of their read.** session-tasks tags each kanban card of the switch's cached copy with `cachedAt` (when its kanban list was read; a machines-only refresh does not move it) and `paintedAt`, and the snapshot with `cachedAt`. Until this chat's kanban read answers, the panel judges those cards' evidence and board status at `cachedAt` (`chipAsOf`), not now: a running card stays running instead of flashing to `status unknown · evidence unknown` for the 2-5 s read. The header shows `· updating…` (tooltip: `Last-known cards from N min ago; refreshing`). Bounded: only within 30 s of the paint (`CACHED_HONOUR_MS`; a read that never answers then shows unknown) and only for a copy at most 30 min old (`CACHED_MAX_AGE_MS`). Process and sub-agent chips are never tagged: always judged at now. The read answering or failing removes the tags (a failed read's kept cards are judged at now: unknown, as before).
- **Reads survive a relaunch.** Card reads (chains), wakes (parks), card titles and chat titles persist to localStorage (`hermes.sessionPanel.reads.v1`, the 500 most recently used of each). A restored answer shows at once and is re-read in the background (a chat title once); a first read still in flight is never saved; a stale answer (failed re-read) stays stale across a relaunch.
- **Pre-computed in the background.** [daveey 2026-11-20] "pre-compute the status panels ... so most switches are instant." No card-change event reaches plugins, so this polls. While the window is visible and focus has been still for 10 s, every 30 s the warm pass takes the chats in `warmTargets()` (the last 8 focused chats, then the 12 most recently active sidebar chats that are not archived; at most 20) and, one chat at a time, each at most every 2 min: re-reads its cards (session-tasks `warm`), asks (user-steps `warm`) and Done list, then walks every waiting card's dependency chain and wake to its leaf (`warmChains`, at most 8 hops, 20 s per chat; it waits only on reads it started, each wait bounded). A switch to a warmed chat paints whole chains, wakes and counts from the first frame.
- **Measured.** Each switch writes one `[panel-timing]` line to `~/.hermes/logs/desktop.log`: `{"session","connection","steps":[[ms,"signature"],...]}`, every distinct thing the panel showed in the 12 s after focus reached the chat. Signature fields: `fresh|cached`, section counts (`a` active, `s` stuck, `w` waiting, `bu` blocked upstream, `r` ready, `q` queued, `u` unasked, `r` reported), asks, done, header pips, `evunk` (evidence unknown), `reading` (chains still being read), root causes.

Tests: `test/instant-switch.test.mjs` (persisted reads, stale and chat-title re-read across a relaunch, bounded waits, warm targets, chain pre-read, trace line), `test/evidence.test.mjs` (cached running and parked cards across the pending read, honour bounds, live chips never judged in the past, first frame; needs `HERMES_CORE_SOURCE`), `../session-tasks/test/instant.test.mjs` (per-card tags, untagging on answer and failure, machines-only refresh). Untested: the kanban endpoint's own 2-5 s latency on the hub is unchanged; only what the panel shows while it runs.

## Person waits pause while hidden

The `session-outcome/person-waits` read runs every 8 s only while the window is visible (`pollWhileVisible`); hidden,
it stops, and showing the window again reads once at once. Test: `test/shared-access.test.mjs`.

## Read-only doors for other plugins

`window.__sessionPanel` also carries `cachedCard(chip, id)` (one card-cache record, including the pushed kanban
`detail` when the gateway feed sent one, its `at`, and `trusted`: the record itself was set by a trusted bundle (an
untrusted ingest clears it) and a chat whose trusted bundle carried it is vouched for by the live feed now; it never
starts a read), `feedVouches(route)` and `feedIsLive(route)`. An untrusted bundle (any chat's) never replaces a trusted
record still within its 60 s freshness, and pushes re-stamp only trusted records.
blocked-panel uses them to take a blocked card's detail without reading it again. Test: `test/shared-access.test.mjs`.

## Pushed from the gateway

[daveey 2026-11-20] "precompute the status panel data on the hub ... kept in sync without polling." When a chat's gateway runs this plugin's backend half (`plugins/session-panel`, its README has the contract), the panel's data for every watched chat (the focused chat plus `warmTargets()`, grouped by connection) is computed there and pushed as `plugin.session-panel.changed`:

- **Registration and catch-up.** `feedCatchUp` POSTs the watched chats to `/api/plugins/session-panel/changes` with the last revision it applied: 1 s after load, when focus reaches a chat that gateway does not watch yet (`feedFocus`), on a revision gap, a new epoch (gateway process restarted: full reset; a restart of the backend for an update restores the feed from its snapshot (`$HERMES_HOME/session-panel/feed-<role>-<profile>.json`, saved at most every 5 s and on stop/exit; restored when at most 15 min old): the feed id is `<host>:<role>` (the hermes subcommand and, for a Desktop's ssh backend, its slot), not the pid, the epoch is kept and revisions jump 1,000,000 past the snapshot's so they stay above anything the old process still pushes, so clients catch up instead of resetting and restored chats are vouched for only after the new process recomputes them; a plugin reload inside the same process resumes the feed's epoch, revisions, bundles and interest at once (on register, else on the next request), vouches for a carried chat again only after recomputing it, and hands a request still on the stopped feed to the resumed one), a batch sent by name only, and every 60 s (`FEED_CATCHUP_MS`). Catch-ups for one connection run one after another; an answer older than a push that landed meanwhile is dropped. A 404 (no backend half; the bridge rejects with `404: …`) or `profile_unsupported` leaves that connection to today's reads and is asked again after 10 min.
- **Trust comes from pushes only.** A connection is live while pushes or the 20 s heartbeats reach this window (`feedIsLive`, 60 s window: three missed heartbeats). A catch-up answer while the connection is not live is an ordinary cached copy: it prepaints, and the panel still reads for itself. The first push or heartbeat that makes it live triggers one full catch-up, trusted. Every push names the chats it vouches for (`fresh`: held, watched and last computed without a failure on the gateway) and re-stamps only those chats' cards, wakes, Done and session-tasks copy (`confirmFeed`, `touchFeed`), so they stay current with no reads; a chat the gateway dropped or keeps failing on is left to age. When pushes stop (another backend took the sockets, no socket, a stuck feed) the stamps stop and after 60 s everything ages and is read as before. A window shown again re-registers its chats at once (the gateway drops chats nobody re-registers for 15 min).
- **Pushes.** `onFeedEvent` matches the feed id and the gateway's own name for its profile (from the catch-up answer), applies a batch only when its `prev` is the revision we have, ignores one we already have, and catches up on a gap.
- **One bundle fills every cache** (`ingestBundle`): session-tasks' per-chat cards and ancestry (`ingestFeed`, republished at once for the focused chat), Done rows, and each chain card's card read, wake and title. A pushed card keeps its record, and its next hop is worked out when read (`cardInfo`), so waits, "10m left" and "overdue" keep counting between pushes.
- **What stops while trusted.** session-tasks does not read a feed-kept chat's kanban list on a switch (only every 60 s, `FEED_KANBAN_POLL_MS`, and that read keeps it feed-kept) and paints it untagged (no "updating…"); `refreshDone` takes Done from the feed; the warm pass skips the connection. Process and sub-agent rows still come live from the chat's runtime.

Tests: `test/feed.test.mjs` (an untrusted answer prepaints and is still read; the first push makes it live and refetches trusted; heartbeats keep entries current for ten minutes with no reads and they age once heartbeats stop; a pushed wait keeps counting; 404, another profile, a transient failure; chain order, duplicate, gap, by-name batch, new epoch, other feed or profile; an older catch-up answer dropped; one catch-up in flight; targets), `../session-tasks/test/instant.test.mjs` (feed-kept copy and ancestry, untagged switch with no kanban read, own read when not re-stamped, re-stamps and untrusted copies).
