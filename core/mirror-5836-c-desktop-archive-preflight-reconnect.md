# Desktop: archive preflight survives reconnects (mirror 5700–5704)

## Behavior
- Archive hides the row optimistically and visibly queues reconnectable transport failures for a bounded 60s budget, retrying the whole ancestry/task safety read on the captured owner/profile.
- Unknown safety results still require explicit confirmation; exhaustion restores the row with an error rather than an override dialog; reload drops the intent and reports cancellation.
- Rows with their own connection keep the default profile; ownerless rows use the invocation profile. All reads share one absolute deadline; late successes are rejected. The diagnostic GET after commit is detached.

## How it works
`reconnect-archive-preflight.ts`, `refresh-archive-tasks.ts`, `archive-commit.ts`, `archive-soft-hide.ts`, `confirm-linked-archive.ts` and `lib/archive-trace.ts` (one archive id logs each phase).

## Code
- `apps/desktop/src/app/session/hooks/use-session-actions/reconnect-archive-preflight.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/refresh-archive-tasks.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-commit.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-soft-hide.ts`
- `apps/desktop/src/lib/archive-trace.ts`

## Tests
Static source evidence (not executed for this documentation audit):

`apps/desktop/src/app/session/hooks/use-session-actions/archive-reconnect.test.ts` uses mocked APIs/fake timers to check reconnect retries, reload cancellation, pinned ownership and detached diagnostic verification. `archive-deadline.test.ts` checks shared deadline deduction, hung reads, late success and restoration without an override. `archive-linked-confirm.test.tsx` checks owner/profile capture at the action hook. `preflight-reconnect-regression.test.ts` checks queueing rather than an unknown-safety override. An actual disconnected Desktop/backend transport is untested.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. Regenerate desktop i18n key inventory.

## Gotchas
- Safety uncertainty must never be treated as safe.
- Reload does not replay a stale safety decision.

## plugin?
No. Desktop UI in the core app.
