# One open-human-ask predicate for the badge, stored outcome and session stripe

Mirror entries 5193-5196.

## Behavior

- `card_is_open_human_ask` is shared by the count filter, the replica bucket, `_card_items` and the row field `needs_you`, so badge, outcome and stripe cannot disagree. The Desktop stripe paints stuck first whenever an ask is open (unless user-blocked/parked). Card block, unblock, complete and archive refresh the origin and shepherd chats' stored outcomes after commit (not inside a caller's open transaction; touch subscribers do not refresh; a refused transition refreshes nothing; archiving a blocked card withdraws the decision).
- `desktop_stripe_report` logs `stripe-invariant-violation` when an ask is open on a non-red, non-parked stripe.
- 5194: `needs_you` is part of the kanban evidence set carried by the retained-row refresh, so a fresh snapshot that omits it clears a stale value; the invariant is reported for live rows only.
- 5195: the invariant is part of the change key, so an ask that opens while the stripe stays non-red is reported; a failed annotation read still drops `needs_you`.
- 5196: released chat links are dropped from the transition refresh (test-only).

## How it works

- Python: `hermes_cli/kanban_open_asks.py`, `kanban_outcome_refresh.py`, `kanban_session_activity.py`, `kanban_activity_identity.py`, `kanban_db_archive.py`, `desktop_stripe_report.py`, `hermes_state_outcome.py`.
- Desktop: `store/session-dot-state.ts`, `lib/session-signatures.ts`, `app/session/hooks/refresh-retained-kanban.ts`, `sidebar/session-row-stripe.tsx`, `types/hermes-session-activity.ts`.

## Code

- `hermes_cli/kanban_open_asks.py`
- `hermes_cli/kanban_outcome_refresh.py`
- `hermes_cli/desktop_stripe_report.py`
- `apps/desktop/src/store/session-dot-state.ts`
- `apps/desktop/src/lib/session-signatures.ts`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_open_asks.py`, `test_desktop_stripe_invariant.py`, `test_kanban_card_transition_outcome_refresh.py` (incl. `test_a_chat_whose_every_link_is_released_is_not_refreshed`), `test_kanban_relay_blocked_reason.py` (`test_failed_read_drops_needs_you`); vitest `session-dot-state.test.ts`, `session-signatures.test.ts`, `refresh-retained-kanban.test.ts`, `lib/session-outcome.test.ts` (including "reports an ask that opens while the stripe stays non-red").

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Outcome refresh is skipped inside a caller's transaction and left to the poller for processes without chat sessions.

## Why core instead of a plugin

The predicate is shared between core Python read paths and the Desktop stripe so they cannot drift; a plugin cannot own both ends.
