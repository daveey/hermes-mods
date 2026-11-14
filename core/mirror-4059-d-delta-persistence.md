# Persistent delta bases and fenced Desktop cache writes

Mirror snapshot 4059; covered ordinals: 3946, 3947, 3948.

## Behavior

- Delta bases can survive a Desktop relaunch and backend roll, avoiding a mandatory whole-body answer when both sides still hold a verified base.
- Persisted bodies are scoped, hash-validated, bounded, and private-mode files. A corrupt/missing base falls back to a whole response; persistence never serves a stale response in place of running the handler.
- Unchanged copies are not rewritten per poll; rate-limited touches keep actively polled bases recent. Desktop async writes cannot undo delete, replace, or synchronous quit flushing.

## How it works

`hermes_cli/web_delta_response.py:BodyStore` optionally writes bodies beneath the home cache, keyed by hashed credential namespace and body digest. Reads validate the digest; bad files are removed. Atomic temporary-file replacement and an mtime-based budget bound storage; memory hits touch or recreate disk copies at most once per minute. Middleware enables this store by default; encoded frames remain memory-only.

`apps/desktop/electron/delta-reads.ts:DeltaBodyStore.persistTo` retains the newest copy per scoped read beneath Electron user data. A versioned header binds key and hash; lazy loading validates both. Writes are debounced, saved hashes prevent unchanged rewrites, and touches are rate-limited. `persistDeltaBases` is wired in `main.ts` and flushes synchronously on `will-quit`.

Patch 3948 serializes async flushes and keeps dirty ownership until publication. Unique temporary names, copy-identity checks, a quit epoch, and validation immediately before synchronous rename fence delayed writers. Explicit deletion is distinct from memory eviction. Synchronous budget trimming also counts interrupted temporary files.

## Code

- `apps/desktop/electron/delta-reads.test.ts`
- `apps/desktop/electron/delta-reads.ts`
- `apps/desktop/electron/main.ts`
- `hermes_cli/web_delta_response.py`
- `apps/desktop/electron/delta-persistence-lifecycle.test.ts`

### Mirrored commits

- 3946: `91cb7cb485673d71f3f55b588fe7e864a21ac1c3` — Persists scoped delta bases across client/backend restarts.
- 3947: `820b90c3ec7fea876ef404da6b48e88be71fb52f` — Retains hot disk bases without rewriting unchanged polled copies.
- 3948: `85123c2e0d27597fc5a688068d654d88bb3fd5cb` — Fences async delta-base publication against delete, replacement, and quit.

## Tests

- `tests/hermes_cli/test_web_delta_response.py`: reopened stores supply decodable/hash-verified deltas through HTTP middleware; corrupt/truncated/empty bases yield whole bodies; another credential/header namespace cannot borrow a base. Tests pin file/directory modes, hash-only path names, bounded LRU eviction, hot-memory refresh/recreation, touch rate limits, malformed-key path rejection, default home-cache wiring, and unwritable-directory memory fallback.
- `apps/desktop/electron/delta-reads.test.ts`: store reopen retains usable request-base headers and verifies a decoded delta; damaged or wrong-scope files cannot be bases. Debounced and quit writes, private modes, failed-delta invalidation, disk budget, userData/quit registration, and unwritable fallback are asserted. Follow-up tests prove unchanged bodies preserve file inode, changed bodies rewrite, and touches/recreation are rate-limited.
- `apps/desktop/electron/delta-persistence-lifecycle.test.ts`: gated mkdir/write operations prove quit drains in-flight dirty ownership and delayed writes cannot resurrect deleted bodies or overwrite replacements/quit output. Memory eviction does not invalidate an older valid disk base. Interrupted temps count toward quit budget; repeated quit-only stores stay bounded with private file modes.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Body payloads may contain sensitive response data even though credentials are not stored; private modes are not encryption. Cache removal/corruption must always be recoverable by a whole response. A verified base is not authorization: the actual handler still runs under the request's credentials. The tests simulate stores, middleware, and app hooks, not live backend-roll or packaged Desktop deployment.

## Plugin?

No. Both protocol endpoints and Electron persistence lifecycle must cooperate inside core; a plugin cannot safely substitute this wire-format/custody logic.
