# Desktop comments follow the registered owner

Patches: **4348** (4352 snapshot).

## Behavior

A drawer comment refused as a foreign-card write can retry once on the registered owner connection while preserving profile and board. An unavailable owner receives a user-facing not-saved explanation instead of worker-only instructions.

## How it works

commentOnOwner parses a refusal for the exact card ID, validates the owner token and resolves it through connectionForHubGateway. A captured complete owner identity is required and same-connection loops are refused. Only the definite foreign-card refusal permits retry; ordinary errors propagate unchanged.

## Code

- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/comment-route.ts`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `apps/desktop/src/plugins/kanban/api.gateways.test.ts`
- `apps/desktop/src/plugins/kanban/comment-route.test.ts`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Comments have no replay key. Never retry a timeout or uncertain second write: inspect the owner's comments first. Malformed owner text is not echoed as instructions. This changes the built-in Desktop Kanban integration, not an independently installable third-party plugin.

## plugin?

Not standalone. The changed files live in the built-in Desktop Kanban plugin and depend on its core API/SDK connection registry.
