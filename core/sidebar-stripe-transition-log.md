# Sidebar status-stripe transitions are logged with their row inputs

Patch **0555** (source `be06ecfc28`). Related: [sidebar-status-stripe.md](sidebar-status-stripe.md).

## Behavior

Rows kept being painted green (done) while Kanban work was still queued, and were right again by the time anyone looked.
Each sidebar row now writes one `desktop.log` line `stripe-transition <sid> old->new` when its stripe changes. The line carries
conn/profile, the dot state, `kanban_active/parked/held/blocked/waiting_on`, `kanban_observed_at`, `goal_active`,
`background_active`, async delegation and background-running claims, and the row source (`sessions.changed`, `list-refetch`,
`window-sessions-changed`, `project-tree`, `cache`). The first sighting of a row only records; at most 6 lines per session per
minute are written, with a count of the suppressed ones.

## How it works

`apps/desktop/src/store/session-dot-state.ts` holds the per-session last-stripe map, the rate cap and the formatter;
`session-row.tsx`, `use-background-sync.ts`, `use-desktop-integrations.ts` and `use-session-list-actions.ts` tag each call with its source.

## Code

`apps/desktop/src/store/session-dot-state.ts`, `apps/desktop/src/app/chat/sidebar/session-row.tsx`, the three hooks above.

## Tests

`apps/desktop/src/store/session-dot-state.test.ts`: one line per transition of a done -> waiting -> done flip with the row inputs;
nothing while the stripe is unchanged; rows outside the page are tagged by origin (project tree, offline cache, refetch);
a flapping session is capped per minute and reports what it skipped.

## Applying

`git am -3`; the sidebar stripe patches must be applied first.

## Gotchas

Logging only: it never changes a stripe. Read it with `grep stripe-transition ~/.hermes/logs/desktop.log`.
