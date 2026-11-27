# Desktop Background section, stripes and bounded reports

**Scope:** Background grouping and counts; stripe-report sender.

**Source ordinals:** 6495, 6496, 6565, 6569, 6570, 6571, 6572, 6573, 6574, 6575, 6576

## Behavior

- Foreign Kanban owners route into the Background section; owner placement no longer depends on the window-scoped pin set (unpinned owners from all gateways included, pinned lineages deduplicated).
- Aggregate counts right-align in the header's trailing slot as colour-outlined tabular badges; the literal owner display prefix is stripped only inside Background.
- Stuck sessions stay visible beside running work with an independent orange count; stalled work is distinguished from waiting; uncertain/expired observations keep unfinished owners and render human asks red and other retained states as a neutral Unknown, never as authoritative zero.
- Linked review waits show a distinct waiting timer from bounded producer-wait evidence qualified by selected board (`kanban_producer_wait.py`): the producer card must be running or in review, match any named gateway and board, and the timer starts at the current park event; missing, terminal or unknown producers supply no evidence, and no ambient board discovery or network read is made. The status stripe for this state is the external/waiting one with reason `linked_card_producer_wait`.
- Stripe-report POSTs are capped at four in flight per connection (FIFO, bounded queue) so a wake burst cannot time out together, and idle budgets are released.
- Background retains unfinished owners past the 180-second observation freshness window until a replacement zero arrives; expired counts are shown neutrally and the freshness clock is Background-only. Running work with activity older than two hours also counts as Stuck.

## How it works

- `owner-chats.ts`, `background-section.tsx`, `session-row-card-squares.tsx`, `session-row-card-timers.tsx`, `session-row-stripe.tsx`, `bounded-diagnostic-sender.ts`.

## Code

Files touched by the series (paths relative to the source tree):

- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/app/chat/sidebar/background-section.tsx`
- `apps/desktop/src/app/chat/sidebar/card-counts-context.ts`
- `apps/desktop/src/app/chat/sidebar/index.tsx`
- `apps/desktop/src/app/chat/sidebar/owner-chats.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-card-squares.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-timers.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`
- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `apps/desktop/src/i18n/types_session_row_status.ts`
- `apps/desktop/src/lib/bounded-diagnostic-sender.ts`
- `apps/desktop/src/store/session-filter.ts`
- `apps/desktop/src/store/sidebar-automated.ts`
- `apps/desktop/src/store/sidebar-card-work.ts`
- `apps/desktop/src/types/hermes-session-activity.ts`
- `hermes_cli/kanban_producer_wait.py`
- `hermes_cli/kanban_replica_upstream.py`
- `hermes_cli/kanban_session_activity_remote_wait.py`
- `hermes_cli/kanban_session_activity_timers.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/sessions_projection_manifest.py`
- `hermes_state_outcome.py`
- `hermes_state_session_status.py`
- `locales/_keys.desktop.json`
- `schemas/fixtures/producer-wait-sidebar.json`

## Tests

Source test files changed or added by the series:

- `apps/desktop/e2e/background-header.spec.ts`
- `apps/desktop/src/app/chat/sidebar/background-section.test.tsx`
- `apps/desktop/src/app/chat/sidebar/background-unfinished.test.ts`
- `apps/desktop/src/app/chat/sidebar/owner-chats.test.ts`
- `apps/desktop/src/app/chat/sidebar/owner-routing.integration.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-squares.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-timers.test.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe-reporting.test.ts`
- `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`
- `apps/desktop/src/app/chat/sidebar/stuck-work.test.tsx`
- `apps/desktop/src/lib/bounded-diagnostic-sender.test.ts`
- `tests/hermes_cli/test_kanban_local_lineage_counts.py`
- `tests/hermes_cli/test_kanban_producer_wait.py`
- `tests/hermes_state/test_session_status_refresh.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6495-6576, 11 commits: 6495, 6496, 6565, 6569, 6570, 6571, 6572, 6573, 6574, 6575, 6576) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Stale sources never create an active wait clock.
- Source commit messages cite focused/sidebar suite results and mutation checks, but those are prior owner claims; none were re-run here, `e2e/background-header.spec.ts` in particular is unexecuted, and live app behaviour was reported unverified by the source author.
- Stripe-report budgets are keyed per connection (default four in flight, FIFO) so a hanging connection cannot hold a healthy one; incident timings in source commit text are intentionally not reproduced here.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Renderer.
