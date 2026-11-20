# Session Panel

The panel displays the current chat's tasks and decisions using the owner-qualified session-tasks and user-steps projections.

## Blocked cards and retired decisions

A canonical decision can be dismissed, withdrawn, or resolved while its owner card still reports `blocked`. Deduplicating the decision must not make that card undiscoverable.

- A request represented in the decision queue continues to own its presentation; no duplicate ask is added.
- Otherwise, a fresh blocked card appears under **Waiting**, with its gateway/profile/board/card identity, current state, and previous request marked **reference only**.
- **Open card** uses the original qualified chip. This status row never submits a decision, drafts a message, or reopens a retired ask.
- Stale rows remain status-unknown; terminal and released cards are not resurrected.
- Queue membership and panel visibility use the same predicate, including pending receipt readback. Receipt-only work keeps the queue mounted even when no actionable asks remain.

The bridge additions `presentationForSource(source)` and `hasQueuePresentation()` are optional for compatibility with older user-steps installations. They are scoped to the focused connection/profile/session; matching uses existing exact-source provenance rules.

## Finished history

Open cards remain visible by default. Done and archived cards share a collapsed **N finished** button; expanding it mounts their rows. Expansion is held only in renderer state, keyed by connection/profile/chat, and resets on remount. A finished-only chat retains the disclosure. Existing Done badges still count only done cards; needs-you and live agent projections are unchanged.

The display-only `/session-cards` feed includes local done/archived cards and settled remote receipt history from the core's gateway/profile-scoped origin projection. Unknown or unverified remote obligations remain live, never reclassified from `reported_status`. Older cores without receipt history retain their existing remote-history limitation.

## Closed remote cards the owner never confirmed

A card on another gateway can report `done` or `archived` while this chat's link to it is never released (the owner confirms a completion on a later pass; a retired worker never does). session-tasks keeps such a card's state `unknown` and carries the owner's report as `reported`. The panel lists these under a collapsed **N reported finished, not confirmed** button, each row reading `reported done by <gateway> · not confirmed by its owner`, and adds `N reported finished, unconfirmed` to the header. They are not under **Waiting**, not counted as done or as evidence unknown, and never ask; they still keep the panel shown. A stale read, or a card with no terminal report, stays status-unknown under **Waiting**.

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

## Waiting says what each card waits on

Folded (the default), the **Waiting** section shows `Waiting · N`, what the rows wait on as counts, and one line per card for the first three (`<title> · <what it waits on>`, then `+N more waiting`), in the same form as the collapsed panel's lines (which also list blocked-upstream rows; folded Waiting lists only its own rows, and a retired ask's status row renders in full instead). Clicking the heading opens the full rows for that chat; the choice is not kept across chats or relaunches. The counts are `on parent · on schedule · contained · in triage · in review · blocked · unknown`, zero buckets hidden; there is no catch-all bucket (`1 other` told the user nothing), and any other lifecycle state counts under its own name. *On parent* = a `todo` card or one waiting on another card; *on schedule* = a scheduled card or any other named wait (timer, CI run, deploy); *contained* = waiting on a contained triage card (`waiting on contained card <id> · release by <authority> (<status>)`); *in triage* / *in review* / *blocked* = those board states (a retired ask's still-blocked owner is *blocked*); *unknown* = the row's status could not be read (stale read, missing or expired evidence, a sub-agent or process without evidence), never guessed from its last known state. An on-parent row that has an upstream leaf appends ` → <id> <status>`. The status row of a still-blocked card whose request was retired stays visible while folded, because nothing else shows it.

A parked card read fresh from its board leads with what it waits on and names the source last: its published wait (`waiting on the clock until …`, `waiting on card t_x since 12:19 → t_x running`), else what its state means — `in triage · waiting to be specified and promoted`, `waiting on review`, `waiting on card <id> (<status>)` for a todo with an upstream, `scheduled · its owner has not published what wakes it`, `blocked · no reason published`. A ready card goes to **Ready** (`ready · waiting for a worker to claim it`) and a todo with no upstream or wait to **Not started**: neither is waiting on anything. A parked card has no run, so its unknown execution evidence is expected and is not counted as `evidence unknown` in the header (that count read as the reason for the wait); running and status-unknown rows still count.

## Running rows are one line

A running card, sub-agent, process or rented machine is one line: its state box, the title (truncated), and on the right where it runs and for how long (`hub · 14 min`, `worker-1 · 2 h`, `pool · 3 h`, `in review · 2 h`, `sub-agent · 14 tools`). A running card with no event for 20 minutes reads `quiet N min` in the waiting colour (Stuck still takes over at 2 h). The tooltip says the same in words (`running on hub · for 14 min · last event heartbeat just now`).

Clicking the row opens its details: card id, assignee, board and gateway; start time and last event; the full execution-evidence line; and **Open card**. A row reaches In progress only with running evidence, so nothing hidden by default changes what the user would do; unknown evidence still lists under Waiting and counts as `evidence unknown` in the header.

The **In progress** heading shows only when another section shares the body; alone, the header's `N running` and blue boxes already name it.

`test/panel.test.mjs` covers the one-line row (the card from the 2026-11-20 screenshot), the click-open details and Open card, quiet/pool/worker/review/sub-agent meta; `test/evidence.test.mjs` covers evidence moving into the details.
