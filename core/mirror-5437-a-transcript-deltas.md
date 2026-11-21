# Push session transcript deltas into the Desktop tail cache

Mirror entries: 5424, 5425.

## Behavior

- The gateway emits a global `sessions.moved` change-watcher event, at most once per second and only when message rows were added, naming each moved session.
- A new REST route `GET /api/sessions/{id}/messages?after_id=N[&limit]` returns the live display rows after row N (bounded: default 200, maximum 500) plus a `delta` block (`anchor_found`, `has_more`, `transcript_revision`, and after 5425 `fold_conflict`).
- The Desktop background prefetcher applies these deltas to each tracked session's cached tail, so opening a chat finds the newest messages already cached instead of waiting for the next listing poll and a 30-second-throttled refetch.
- When a delta cannot be trusted (anchor row gone, gap, rotation, in-place rewrite, fold conflict, cache moved underneath, or an older core without the route) the prefetcher falls back to refetching the latest page.
- The periodic listing pass becomes a 60-second safety net when the gateway pushes (5 seconds otherwise). A moved session the prefetcher does not know asks for an early listing pass, rate-limited; a mover not covered by any listed connection/profile asks for none, and one an early pass did not pick up is ignored for 10 minutes.
- A gateway that answered without the `after_id` route is probed again after 10 minutes.

## How it works

The change watcher's signature is each watched store's maximum message id behind the kept observer's commit key (one PRAGMA while idle). The event payload reads the id range since the last frame, so a floored window loses nothing. The event is added to the gateway contract (events, regenerated TypeScript and OpenRPC).

The REST route reads the transcript revision first, then the rows. In 5425 the revision also changes when a row's active/compacted flag changes (queued-prompt drain, model-switch pivot, rewind, compaction) and sums the resume lineage, so a cached tail extended by deltas refetches rather than keeping a row a fresh page would hide. A new row the latest page would fold into an earlier slot is reported as `fold_conflict`. Sessions the listings never show (hidden, tool, subagent, delegate children) are not pushed.

In the Desktop, the renderer forwards every socket's `sessions.moved` over a fire-and-forget IPC to the main process. The prefetcher keeps one fetch in flight plus one queued per session. The first push for a session learns its revision from a page; later pushes apply deltas through a new cache store operation (`putAfter`, with `tailId` to read the cached tail).

## Code

- `tui_gateway/change_watcher.py`, `tui_gateway/contracts/events.py`
- `hermes_cli/web_routers/sessions.py`, `hermes_state_stored_reads.py`, `hermes_state_common.py`
- `apps/desktop/electron/transcript-prefetch.ts`, `transcript-cache.ts`, `transcript-cache-host.ts`, `transcript-cache-client.ts`, `preload.ts`
- `apps/desktop/src/store/transcript-push.ts`, `apps/desktop/src/main.tsx`, `apps/desktop/src/global.d.ts`
- `apps/shared/src/gateway-contract.generated.ts`, `apps/shared/src/gateway-contract.openrpc.json`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Gateway tests cover the `sessions.moved` watcher event and the `after_id` route, including the 5425 revision, lineage and fold-conflict cases. Desktop tests cover the prefetcher push path (delta equals a fresh page across fallback cases, re-probe and ignore windows, early-pass limits), the host wiring with an older core lacking the route, the cache store, and the renderer forwarder. Renderer-to-main IPC in a packaged app is untested.

- `tests/tui_gateway/test_change_watcher_sessions_moved.py`
- `tests/hermes_cli/test_session_messages_after.py`
- `apps/desktop/electron/transcript-prefetch-push.test.ts`
- `apps/desktop/electron/transcript-cache-host.test.ts`
- `apps/desktop/electron/transcript-cache.test.ts`
- `apps/desktop/src/store/transcript-push.test.ts`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

Deltas are an optimisation: correctness relies on the full-page fallback, so any mismatch in revision costs a refetch, not stale data. Events are coalesced to one per second, so consumers must not treat them as per-message. Related cache design: [desktop-session-ref-transcript-cache.md](desktop-session-ref-transcript-cache.md) and [desktop-transcript-cache-worker-thread-2215.md](desktop-transcript-cache-worker-thread-2215.md).

## Why core instead of a plugin

The event comes from the gateway's change watcher, the route reads session-store internals and revisions, and the consumer is the Desktop main-process cache; these span core store, gateway contract and Electron host, which a plugin cannot extend consistently.
