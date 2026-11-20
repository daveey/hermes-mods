# Desktop sidebar freshness after gateway probe failure

Mirror snapshot 5011; ordinals 4891, 4892.

Entries:

- **4891**: Recover sidebar freshness after gateway probe failure
- **4892**: fix(desktop): scope probe recovery retries to answerable owners

## Behavior

- After gateway-unavailable invalidation, the existing stale-owner backoff is scheduled (15/30/60/60 s). Only an authoritative successful list restores freshness; cached rows stay fail-closed.
- Probe recovery retries are scoped to answerable owners on the request gateway. Switching away cancels retries; switching back re-arms. A single-profile success ends that profile's retries without claiming other profiles are fresh.

## How it works

- `apps/desktop/src/store/offline-sessions.ts` and `use-session-list-actions.ts`.

## Code

Shipped non-test paths (union over these entries):

- `apps/desktop/src/app/session/hooks/use-session-list-actions.ts`
- `apps/desktop/src/store/offline-sessions.ts`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `apps/desktop/src/app/session/hooks/use-session-list-actions.test.tsx`
- `apps/desktop/src/store/offline-sessions.test.ts`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Status success alone cannot validate session rows. Cached `offline_cached` still suppresses authoritative outcome presentation.

## Plugin?

None.
