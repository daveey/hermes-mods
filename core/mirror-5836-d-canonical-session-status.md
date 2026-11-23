# One canonical session status across Desktop surfaces

Source patch ordinals: 5738–5742, 5786–5789, 5830–5831.

## Behavior

Desktop rows, tab accents, status dots and Bot roster dots consume the backend's versioned status envelope instead of independently inferring completion from activity, counts or client clocks. Missing, unsupported or malformed envelopes are explicitly unknown, not heuristic green. Never-started sessions are labelled draft; published running work retains its running label and accent dot.

Turn admission publishes running status before transcript/provider preparation. Explained retained-card waits stay agent work rather than becoming generic human asks; notification subscriptions do not adopt another owner's human decision. Acknowledged process exits are not left in the failure list.

## How it works

`canonicalOutcomeStatus` validates `status_version`, identity, generation, revision, coverage and presentation. Published `presentation.stripe` is authoritative; `none` paints no stripe. Green additionally requires complete coverage and kind `done`. Mounted consumers report committed paints with revision and mount-lifecycle qualifiers to the row's owning gateway through `/api/sessions/{id}/stripe-report`; legacy reason-only reports remain a separate diagnostic.

Malformed source controls or goals cause unknown source coverage, while a provider with no contribution remains supported. `turn_start` refresh runs at admission, with the asynchronous gateway path dispatching publication through `asyncio.to_thread`. The retained-wait classifier limits its exemption to the retained episode and explained parent/recorded waits; a later explicit ask remains an ask. Scheduling reads back the resulting state instead of announcing success for a no-op.

## Code

- `apps/desktop/src/lib/session-outcome.ts`
- `apps/desktop/src/store/committed-stripe-report.ts`
- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/plugins/hermes-bots/bot-row.tsx`
- `hermes_cli/desktop_stripe_report.py`
- `hermes_state_session_status_snapshot.py`
- `gateway/turn_outcome.py`
- `tui_gateway/turn_outcome.py`
- `hermes_cli/kanban_retained_wait.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

Source coverage includes mounted row/tab/dot agreement, revision repainting, unknown protocols, Bot roster propagation, stale and out-of-order paint reports, malformed control/goal recovery, REST/CLI outcome parity, gateway and TUI admission publication, and retained waits versus explicit/subscribed asks.

Key suites: `apps/desktop/src/app/chat/session-canonical-surfaces.test.tsx`, `apps/desktop/src/app/chat/bot-row-status.test.tsx`, `tests/hermes_cli/test_desktop_stripe_invariant.py`, `tests/hermes_cli/test_session_outcome_surfaces.py`, `tests/hermes_state/test_session_status_refresh.py`, `tests/gateway/test_turn_start_outcome.py`, `tests/tui_gateway/test_turn_outcome_refresh.py`, and `tests/hermes_cli/test_kanban_retained_wait_status.py`. The TUI fixture follow-up isolates background link revalidation from inline turn fixtures.

## Applying

Keep the backend envelope producer, gateway contracts and Desktop consumers together in the ordered mirror series. An older backend intentionally renders unknown. The admission and retained-wait follow-ups depend on the preceding canonical-status implementation.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Activity remains useful decoration, not status authority. A paint report is an observation, not a source of canonical status or proof that an unmounted surface agrees. Publication failure is logged rather than refusing an already-admitted turn. This mirror pass does not demonstrate installed-client convergence.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
