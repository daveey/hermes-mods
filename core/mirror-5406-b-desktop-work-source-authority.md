# Desktop: an unavailable work source holds an idle row off done/green; authority is captured per owner

Mirror entries 5147-5151.

## Behavior

- A failed task/process/subagent/lineage read publishes a qualified veto keyed by connection, profile, stored id and source. A completed stored outcome cannot paint a row done while a source is unknown, or while work is background/blocked/waiting. A successful read clears only that source's veto; another source succeeding does not. Nothing is written to tasks or outcomes.
- 5148 narrows the old pin "outcome wins over derived inputs": a stored done no longer hides known unfinished work.
- 5149: authority is captured at the real idle-row hook with request tokens and epochs. Required sources must all be read for the current owner generation; tokenless successes, wrong scopes and obsolete runtime authority do not certify; a pending or failed read fails closed; owner changes (including A to B to A), reconnect, close, LRU eviction and primary replacement revoke earlier replies; durable proofs survive runtime replacement.
- 5150: authority observation runs outside render, and mounting an unread secondary row does not update another row; the owner row index prefers first-row lineage.
- 5151: runtime epoch tracks each runtime's effective roles (mirror, tile, main), so rebinding a tile or main pane between already mirrored runtimes revokes cached proof; same stored ID on different connection/profile routes keeps independent authority (exact id outranks lineage alias); the draft candidate index is built once per list change.

## How it works

- `apps/desktop/src/store/work-source-owner.ts` (owner/epoch index), `session-work-source.ts`, `session-control.ts`, `session-event-owners.ts`, `session-states.ts`, `session.ts`, `session-dot-state.ts`.
- `apps/desktop/src/lib/use-work-source-unavailable.ts` and `sdk/sessions.ts` feed the row stripe and tab accent in `session-row-stripe.tsx` / `session-tab-accent.tsx`.

## Code

- `apps/desktop/src/store/work-source-owner.ts`
- `apps/desktop/src/store/session-work-source.ts`
- `apps/desktop/src/store/session-control.ts`
- `apps/desktop/src/store/session-dot-state.ts`
- `apps/desktop/src/lib/use-work-source-unavailable.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.tsx`

## Tests

Source tests (not executed in this mirror pass; vitest):

- `session-work-source.test.ts`, `session-work-source-authority.test.ts`, `work-source-owner.test.ts`, `work-source-secondary-authority.test.ts`, `session-control-authority-routing.test.ts`, `session-control-owner.test.ts`, `session-row.test.tsx`, `session-tab-accent.test.tsx`, `lib/session-outcome.test.ts`.
- Named cases include: unread authority vetoes a stored-done tab, request token not reused after reset, ABA owner revocation, same-ID rows on two live routes, tile rebind between mirrored runtimes, owner index built once, secondary owner revoked on close.

The real-time structural-cost figures quoted in commit text are untested here.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Fail-closed by design: a stale or unreachable source keeps rows from going green; expect more rows to stay amber after a reconnect until the first read lands.
- Index cost is bounded by keying on list identity; avoid per-render rebuilds.

## Why core instead of a plugin

This is the Desktop renderer's core session store and sidebar; it is not behind the plugin SDK boundary.
