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

## Waiting rows

A waiting card is one line, laid out like a running row, and every line of a list shares one grid so the reasons line up: its box (yellow outline = waiting, pink = blocked upstream, the larger "H" box for a hub card), its title, then a short reason (a fixed 42% column in every list, so reasons start at the same place folded, opened and in Blocked upstream, and the title keeps the rest) and how long it has waited (from the card's published wait start; blank when none). The section is folded by default: `Waiting · N`, the counts of what the rows wait on, and the first three lines (then `+N more waiting`); the collapsed panel keeps the same lines, and also lists blocked-upstream and retired-ask lines. Clicking the heading shows every line; the title is the line's disclosure button (Tab, Enter/Space; a click anywhere on the line works too, and a hover tint shows it opens), and the linked card name is its own button; opening a line shows its long form: the full wait text (cards linked), the card id, state, where it runs and where the state was read (`read from board status`), its execution evidence, and **Open card**.

**Outline.** Cards that wait on another card of the same list are shown under it, indented, so a chain reads as one tree (Card A · on CI run 7 / Card B / Card C / Card D under C). A card's blockers are the cards its published wait names, its upstream, the card that releases it when contained, and its blocked-upstream parent; the first of those in the list is its parent. A nested line drops only the "on <parent>" reason (the indent says it) and keeps its own age; any other reason (a CI run, "released by <card>", a warning) stays. A card whose blocker is not in the list is a root and names the blocker. A cycle never nests; a card that leads into a cycle still nests. The indent is capped at four levels. The folded and collapsed cut (three lines) is taken from the outline, so children stay under their parent, and roots are ordered by the most urgent line in their subtree (blocked upstream, then a warning such as well overdue), so the cut never hides those behind a calm chain.

The counts are `on parent · on schedule · contained · in triage · in review · blocked · unknown`, zero buckets hidden; there is no catch-all bucket, and any other lifecycle state counts under its own name.

The short reason (`rowWait`), built from the card itself, never from the long text:

| the card | line |
|---|---|
| waits on another card | `on <its title> · <status>`: the title linked, cut to 28 characters (whole title, id and status in its tooltip); the status is the one the wait names, else the card's known state in this chat |
| a todo with an upstream | the same, for the upstream card |
| contained | `released by <card>` |
| blocked upstream | `parent blocked: <card>` |
| a clock wait | `10h left · Sat 00:00 PST` (Pacific time; `Dec 2 22:20 PST` beyond six days) |
| a clock wake past due | `10m overdue · due Fri 13:50 PST`; from 30 minutes late `⚠ 4h overdue`, orange |
| a CI run / PR / other named wait | `on CI run 44819160264`, `on CI on PR #7`, `on deploy abc123` (URLs are shortened) |
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
