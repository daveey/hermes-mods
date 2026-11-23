# Delta reads on the primary SSH scope

Source patch ordinals: 5764.

## Behavior

Desktop delta-read binding accepts the primary SSH connection's empty-string scope key instead of treating it as absent. An actually undefined scope still has no cache identity.

## How it works

Origin binding now checks `typeof scope === "string"`; URL cache keys reject only `undefined`. The empty string participates in the same scope-plus-path/query key format as other SSH scopes, preserving `sshScopeKey(null)` semantics.

## Code

- `apps/desktop/electron/delta-reads.ts`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`apps/desktop/electron/delta-reads.test.ts` adds a primary SSH connection case whose scope is the empty string and checks its delta binding.

## Applying

Apply the Electron delta-read change alongside its existing delta protocol dependencies, then rebuild the client through the normal build process.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Empty string and missing scope are intentionally different. This change enables requesting deltas; it does not claim every response is incremental or prove live tunnel performance.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
