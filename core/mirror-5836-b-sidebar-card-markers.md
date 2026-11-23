# Separate session circles and linked-card squares

Source patch ordinals: 5549, 5554–5555.

## Behavior

Sidebar rows pair a session-status circle with an always-present linked-card run square. Idle circles stay hollow grey regardless of project color; running cards use blue, worker waits green, unseen results violet, and attention remains amber. Malformed non-array observations do not break row rendering.

## How it works

`SessionRowRunSquare` is inserted after the leading session marker in both row layouts. Running counts are removed from the trailing cluster. Fresh card-kind execution observations refine aggregate live counts into queued versus running work, deduplicated by execution identity and clamped to the aggregate. `selectExecutionObservation` handles evidence freshness/conflicts. Labels and tooltips use shared localization keys; an array guard rejects malformed observation containers.

## Code

- `apps/desktop/src/app/chat/session-status-dot.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row-card-squares.tsx`
- `apps/desktop/src/app/chat/sidebar/session-row.tsx`
- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/i18n/types_session_row_status.ts`
- `apps/desktop/src/i18n/overlay-gaps.json`
- `locales/_keys.desktop.json`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`apps/desktop/src/app/chat/session-status-dot.test.tsx` covers hollow idle styling and active size. Sidebar `session-row-card-squares.test.tsx` covers counts, queue identity/freshness, distinct colors, accessible labels and tooltips. `session-row.test.tsx` covers placement in both row modes, worker-wait rendering, and object/number/boolean observation inputs.

## Applying

Apply UI, localization types and key inventories together, including both color-semantic and malformed-input follow-ups. Rebuild Desktop to deliver the component changes. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Aggregate active counts alone cannot distinguish queued work from execution. Expired or conflicting observations fall back to the aggregate interpretation; queued counts cannot exceed live work. A card run square is not the chat turn's activity indicator.

## plugin?

Core Desktop sidebar, not a standalone plugin. The Kanban data feeds it, but marker placement is in the chat row.
