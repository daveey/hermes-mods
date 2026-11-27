# Durable send outcomes and accurate composer recovery

Patch coverage: 6365, 6366, 6367, 6368, 6373.

## Behavior

A steer refused by both the active turn and server queue keeps its words in the composer and says so. Retried journaled sends say could not confirm only when an earlier attempt may have been admitted; definite pre-send abort/refusal is distinguished from unknown outcome.

Once any attempt of the same send is unknown, a later refusal or abort cannot downgrade it to definitely not sent.

## How it works

Prepared submissions store outcome and earlierUnknown alongside stable send identity. In-flight journaling clears a current attempt's outcome but preserves unknown prior attempts under the same id. Recovery wrappers mark resubmission after an unanswered timeout or other nondefinitive failure before replaying.

If journal read fails, retained in-memory unknown evidence is preserved; a fresh send is not made historically unknown merely by that failed read. Outcome-save failure remains conservative and does not hide the original refusal. Tests spy the actual page storage used by the retry path rather than a disconnected substitute.

## Code

Affected implementation and integration paths in the source series:

- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submissions.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submission-outcome.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submission-outcome.test.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-resume-failed.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-legacy-identity.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submission-outcome-read-failure.test.ts`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Unknown means uncertain, not successful delivery. A later definitive refusal cannot disprove an earlier timeout's admission. Draft retention location and send certainty are different UI facts. No live message was sent or queue recovery exercised in this documentation run.

## Why core, not a plugin?

The host submission journal, transport retry wrappers and composer error display share a send identity across attempts; a plugin cannot safely infer or rewrite that history.
