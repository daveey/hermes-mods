# Blocked notifications show the requested human step

Mirror entries: 4850, 4851.

## Behavior

Blocked Desktop notifications show the structured human action, not the worker free-text status reason. Without a usable action they say the task was paused without naming a step.

## How it works

The notification reader accepts human_request as an object or JSON text, validates an action string, normalizes whitespace and clips action/why text to the notification bound. A valid ask retains the needs-input title and adds why_human detail. Invalid or missing asks use neutral paused wording without inventing an action or exposing the status reason as the request. Localization adds the associated strings, and the default-board socket fixture now carries the structured ask.

## Code

- `apps/desktop/src/plugins/kanban/completion-notify.ts`
- `apps/desktop/src/plugins/kanban/i18n.ts`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Completion-notify tests cover structured and JSON-text asks, malformed/missing actions, clipped plain-text output and reasons that differ from the action. The default-board notification pipeline test asserts that the event action becomes the delivered body.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `apps/desktop/src/plugins/kanban/api.default-notify.test.ts`
- `apps/desktop/src/plugins/kanban/completion-notify.test.ts`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This does not change block-loop notification behavior or create a missing human request. Writers must still provide a concrete action; a technical status note is not a substitute.
