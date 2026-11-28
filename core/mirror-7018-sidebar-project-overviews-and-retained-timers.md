# Sidebar filtered project overviews, queued CI waits and retained card timers

Patch coverage: 6627, 6628, 6652, 6667, 6668, 6722, 6723, 6725, 6726, 6727, 6728, 6729.

Related: the sidebar navigation-block hide (ordinal 7018) is mapped to `mirror-6595-c-desktop-sidebar-search-and-new-session-row.md`.

## Behavior

- **Filtered overviews.** When the sessions filter (e.g. a lanes contribution) narrows the set, project overview previews show the *matching* sessions rather than unfiltered live rows or a capped backend snapshot; headings with no matching rows are dropped and stale pagination is hidden.
- **Shared card counts.** A `CardCountSquares` component renders number-inside-square card counts per category over a shared palette; the sidebar uses it and the Desktop plugin SDK exports `CardCountSquare`, `CardCountSquares`, `CARD_COUNT_CATEGORIES`.
- **Queued CI.** Confirmed queued CI waits show on the session dot; unknown dot states fail closed.
- **Retained timers.** When annotation snapshots have gaps, the last-confirmed *running* and *blocked* row timers are kept for fifteen minutes, scoped by gateway/profile/session, dimmed and labelled with last-confirmed age. Offline rows never show or refresh retained evidence; known-empty rows clear it; waiting clocks are not retained. Retention also works on flat rows, clears when list data arrives, tolerates unhydrated boot project timer lanes, and covers project-level timers.
- Server side: holder fairness orders by stalest published read (`stalest_first`, `priority`, `read_priority`) without writing profile state.

## How it works

`filtered-overview.ts` (`filteredOverview`), `use-overview-previews.ts`, `gateway-project-rows.ts` (`projectMembership`), `overview-row.tsx`, `session-row-timer-memory.ts` (`rememberRowTimers`, `rowTimers`), `session-row-card-timers.tsx`, `session-row-card-squares.tsx`, `components/ui/card-count-squares.tsx`, `session-status-dot.tsx`, `lib/session-outcome.ts`, store updates in `store/project-tree.ts`/`session.ts`; i18n key plus a known overlay gap for the new label; schema fixture `schemas/fixtures/queued-ci-sidebar.json`; the queued-CI dot also has a backend half (`hermes_state_outcome.py` +4 lines, with `tests/hermes_state/test_queued_ci_status.py`, `test_session_status_queued_ci.py` and the `ci_wait_dot_projection.py` fixture), so it is not renderer-only; `hermes_cli/kanban_annotation_fairness.py`.

## Code

All under `apps/desktop/src/app/chat/sidebar/` and `apps/desktop/src/components/ui/card-count-squares.tsx`, `apps/desktop/src/sdk/index.ts`, plus the server files above and `website/docs/developer-guide/desktop-plugin-sdk.md`.

## Tests

Read from source; **not executed here**; no full Electron end-to-end run is claimed. Vitest/Testing Library: `filtered-overview(.integration).test.ts(x)`, `overview-row.test.tsx`, `gateway-project-placement-oracle.test.tsx`, `session-row-timer-memory.test.ts`, `session-row-timer-retention.test.tsx`, `session-ci-wait-dot.test.tsx`, `session-row*.test.tsx`; Python fairness/holder gates. Actual rendering in a packaged app is **untested** here.

## Applying

Apply in ordinal order; the renderer commits assume the earlier sidebar series.

## Gotchas

- Retained timers are stale evidence and are explicitly dimmed; never refresh them for offline rows.
- An unknown dot must not render as green/idle.

## Why core, not a plugin?

Desktop renderer and its SDK.
