# Desktop: bounded structured error details and disk-full recovery classification

Mirror snapshot 5011; ordinals 4355, 4356, 4420.

Entries:

- **4355**: Preserve bounded structured Desktop error details
- **4356**: Preserve disk-full recovery for structured errors
- **4420**: test(desktop): pin inclusive diagnostic depth boundary

## Behavior

- A plain object thrown by an RPC or HTTP boundary used to render as a generic "failed" fallback, twice, and lose its useful detail. The Desktop error-diagnostic helper now extracts a readable message from the object's own data properties (`detail`, `data`, `error`, `message`) and shares that extraction with Desktop logging.
- The walk never serializes the thrown object and never invokes accessors or `toJSON`. It is bounded to six levels, 32 objects and 8192 message characters; only known classification codes up to 128 characters are retained. Legacy `Error` stack handling, string handling and recovery classifications are preserved, a redundant fallback title is omitted, and specialized refusal handling is unchanged.
- 4356: explicit `disk_full` codes were being classified as generic storage failure because codes-first normalization hit the storage rule first. The explicit code is now recognized by the earlier disk-full rule, so the right recovery text and category show.
- 4420: pins the inclusive depth boundary (depth 6 extracts, depth 7 falls back) so a boundary off-by-one is caught.

## How it works

- `error-diagnostic.ts` owns the safe normalization; `notifications.ts` uses it for readable errors and logging.
- Cycle detection, node count, text length and code allowlist limits are constants checked on every step; own data properties are read via property descriptors so getters are never run.

## Code

Shipped non-test paths (union over these entries):

- `apps/desktop/src/lib/error-diagnostic.ts`
- `apps/desktop/src/store/notifications.ts`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `apps/desktop/src/lib/error-diagnostic.test.ts`
- `apps/desktop/src/store/notifications-objects.test.ts`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Only objects with the recognized own fields are normalized; anything else still falls back.
- The diagnostic type-check needed a larger Node heap than the default when the change was authored.
- No live Desktop deployment was verified by these entries.
