# Desktop stripe: red reasons and stale prompts

Patches: **3138, 3154, 3170**.

## Behavior
- A dead runtime's leftover prompt no longer keeps a chat row red, and every red row reports why (a bounded stripe report that the CLI can read back, routed to the owning profile).
- A turn-live sibling runtime is never retired as a superseded prompt; a prompt that moves to another runtime is re-reported.
- On reconnect, claims that are turn-live only (after Stop, before the terminal event) are downgraded so a restart does not keep a dead prompt and red stripe forever.

## How it works
- 3138 adds the stripe report (`desktop_stripe_report`, sessions API/router, outcome command) and superseded-prompt/live-turn store logic.
- 3154 adds a `$stuckPromptInputsKey` computed store as an effect dependency and fixes the superseded rule.
- 3170 makes `reconcileBusyStatesOnReconnect` and the tile delegate's `retireBusyClaim` handle turn-live-only claims, and only needs-input rows track prompt inputs.

## Code
- `apps/desktop/src/store/session-states.ts`, `session-states-superseded-prompts.ts`, `session-states-live-turn.ts`, `session-dot-state.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`, `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts`, `apps/desktop/src/api/sessions.ts`
- `hermes_cli/desktop_stripe_report.py`, `hermes_cli/sessions_cmd_outcome.py`, `hermes_cli/web_models.py`, `hermes_cli/web_routers/sessions.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_session_outcome_surfaces.py`: `test_stripe_report_is_kept_and_read_back_by_the_cli`, `test_stripe_report_routes_to_the_owning_profile_and_ignores_other_sessions`, `test_stripe_report_file_is_capped_and_reasons_bounded`.
- Desktop test files (`session-dot-state.test.ts`, `session-states-stale-prompt.test.ts`, `session-states-reconnect.test.ts`, `session-row-stripe.test.tsx`) were changed; their test names are not in the `def test_` form scanned, so desktop-side coverage is not itemized here.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Reporting stripe reasons is observed from the sidebar effect; it depends on the prompt-input key store from 3154.
