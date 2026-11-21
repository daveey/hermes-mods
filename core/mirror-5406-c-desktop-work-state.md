# Desktop lineage-aware work-source status and settled decisions

Mirror entries: 5294, 5295, 5296, 5310, 5312. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Unfocused finished chats can paint green from their stored done state without first being opened, provided the current route is live and no current pending/failed required-source read vetoes it. Displayed chats (main pane or tile, including a lineage alias) still require positively complete work-source evidence.

Externally running sessions use a filled light-blue status dot and externally down sessions red. A decision list whose items are explicitly settled is an agent-side waiting state, not a human-attention stuck state; unknown/legacy or unresolved items remain conservative.

## How it works

The unavailable-work-source hook tracks lineage IDs across every loaded row source (normal/cron/messaging/project tree/unlisted owner rows). Display roles decide whether positive completeness is required. Undisplayed-row vetoes compare failed/pending observations with the authority epoch belonging to that alias; obsolete-epoch evidence is ignored, but a read begun without authority still vetoes. No live authority also vetoes.

External-dot inline colors are fixed to `#38bdf8` and `#ef4444`, avoiding class-resolution ambiguity. Outcome mapping treats a truthy recorded resolution or resolved/superseded/cancelled/withdrawn state as settled only when every item qualifies and the list is nonempty.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/lib/session-outcome.ts`
- `apps/desktop/src/lib/use-work-source-unavailable.ts`
- `apps/desktop/src/store/session-work-source.ts`
- `apps/desktop/src/store/work-source-owner.ts`
- `apps/desktop/src/types/session-outcome.ts`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Desktop source tests assert row/tab green without unread-source veto, pending-read suppression, displayed-chat positive proof, lineage alias authority, every loaded row list, secondary authority behavior and no-authority reads. The status-dot test checks computed color for running-external/down. Session-dot outcome tests check settled decision requests awaiting agent delivery rather than human attention. These are component/store tests, not screenshots or a live multi-window gateway test.

Relevant source suites/fixtures:

- `apps/desktop/src/app/chat/session-status-dot.test.tsx`
- `apps/desktop/src/app/chat/session-tab-accent.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`
- `apps/desktop/src/store/session-control-authority-routing.test.ts`
- `apps/desktop/src/store/session-dot-state.test.ts`
- `apps/desktop/src/store/work-source-secondary-authority.test.ts`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Do not relax the displayed-chat evidence requirement or use a root-row epoch for every alias. A row absent from all loaded owner lists lacks tracked authority and remains vetoed. Settled does not mean delivered; it changes who is waiting, not execution completion. Unspecified decision item shapes remain stuck.

## Why core instead of a plugin

Shared owner/lineage stores and core outcome/status components must agree across sidebar, tabs and displayed panes; a plugin cosmetic override could mask real pending/failed authority reads.
