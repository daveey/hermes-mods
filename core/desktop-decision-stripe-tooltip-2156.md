# Keep decision explanations while linked work runs

Patch **2155**.

## Behavior

A red decision stripe can still name its open question while a linked card or background job is running. The chat's own live turn continues to take precedence over the stored outcome, as does a user park. This aligns the explanation with the stripe's existing priority rather than creating a new status.

## How it works

`outcomePaintsStripe(state, userBlocked)` centralizes `!userBlocked && !hasOwnLiveTurn(state)`. Both `sessionStatusStripe` and the `outcomePainted` value returned by `useSessionRowStripe` use it. `SidebarSessionRowImpl` passes that value to `SessionRowStatusStripe` instead of independently using `hasLiveTurn`, which also counts background work.

## Code

- `apps/desktop/src/store/session-dot-state.ts`: shared outcome-eligibility predicate.
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`: hook return value.
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`: predicate result supplied to stripe rendering.

## Tests

Source tests inspected; **not executed in this mirror pass**:

- `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx`: `a red decision stripe on a chat whose card is running still names the question` checks background state, the stuck stripe, outcome eligibility and the question in `aria-label`.
- Same file: `the chat's own live turn still beats the outcome, for the stripe and its tooltip alike` checks that working state does not paint the outcome or a stuck stripe.

No live sidebar hover was exercised; the component test verifies the accessible explanation. User-park precedence is retained by the shared predicate, not separately exercised by these two new cases.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. This patch is not standalone.

## Gotchas

Background work and the chat's own live turn are deliberately different. A generic “anything is running” tooltip gate would split color and explanation again. Starting linked work does not clear an open decision.

## Could this be a plugin?

Not cleanly: the built-in sidebar owns both status derivation and explanation rendering. A plugin cannot keep their priority identical without replacing core row behavior.
