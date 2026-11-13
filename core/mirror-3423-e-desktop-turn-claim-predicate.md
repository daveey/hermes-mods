# Desktop: single holdsTurnClaim predicate

Patches: **3184, 3205**.

## Behavior
- The Desktop app decides "this session holds a turn claim" with one predicate over `busy`, `awaitingResponse` and `turnLive`, instead of repeating the three-way check in each store/hook.
- The update-held-prompt idle check (`holdsUpdateHeldPromptIdle`) goes through the same predicate, so tool-part rendering and session state agree.

## How it works
- 3184 adds `holdsTurnClaim(state)` in `session-states-live-turn.ts` and uses it in `session-states.ts`, `native-notifications.ts` and the session tile delegate hook; the reconnect test is adjusted.
- 3205 routes `holdsUpdateHeldPromptIdle` and a `tool-parts.ts` call through it.

## Code
- `apps/desktop/src/store/session-states-live-turn.ts`
- `apps/desktop/src/store/session-states.ts`
- `apps/desktop/src/store/native-notifications.ts`
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts`
- `apps/desktop/src/lib/chat-messages/tool-parts.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/src/store/session-states-reconnect.test.ts` was adjusted in 3184 (no new test names); no test directly covers `holdsTurnClaim` itself (untested).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
