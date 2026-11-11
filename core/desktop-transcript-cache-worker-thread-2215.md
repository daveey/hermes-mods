# Desktop transcript cache SQLite on a worker thread

Patches:

- `patches/2194-fix-desktop-transcript-cache-SQLite-runs-on-a-worker.patch`
- `patches/2195-fix-desktop-transcript-cache-client-never-throws-out.patch`
- `patches/2196-test-desktop-a-transcript-cache-worker-call-with-no-.patch`

The Desktop's synchronous `node:sqlite` transcript cache ran on Electron's main thread; under disk pressure a single write stalled the main loop for over a second.

## Behavior

- The store runs in `transcript-cache-worker.ts` (bundled as `dist/electron-transcript-cache-worker.js`); the main process talks to it through `transcript-cache-client.ts`. Prefetch does one batched `metaMany` per pass and awaits each `putLatest`; renderer put/drop/clear/migrate are posted; the renderer's sync read gets its `returnValue` from the worker (1.5 s cap, then a miss).
- A dying worker is restarted up to `MAX_WORKER_STARTS` (3) times, then the store falls back to the main thread and logs it. Operations slower than 250 ms are logged with their thread.
- 2195: `call()` never throws synchronously; when neither worker nor in-thread store can open the file it rejects, the failure is remembered and logged once, and the sync read answers `null`. Worker calls without their own timeout give up after 30 s.
- 2196 adds the missing test that a call with no timeout rejects at `CALL_TIMEOUT_MS`, drops the late reply, and the next call still works.

## How it works

The host (`transcript-cache-host.ts`) builds the client from the worker file path that `main.ts` passes; the prefetcher is made async-aware.

## Code

`apps/desktop/electron/transcript-cache-{worker,client,host,prefetch}.ts`, `main.ts`, `apps/desktop/scripts/bundle-electron-main.mjs`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`transcript-cache-client.test.ts`, `transcript-cache-host.test.ts`, `transcript-prefetch.test.ts`, `apps/desktop/scripts/bundle-electron-main.test.mjs`, `tests-js/desktop-builder.test.mjs`: the event loop keeps turning during a 1 s store op (with an in-thread control that stalls); the real worker bundle round-trips; restart/fallback/timeout; the bundle runs from its text alone; an unopenable cache answers null.

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

The renderer still blocks on its sync read; only the main loop is freed. The worker bundle must be emitted by the desktop build, otherwise the cache runs in-thread.

## Plugin

None.
