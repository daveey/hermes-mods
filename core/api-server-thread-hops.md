# API server: worker-thread hops land before assistant.completed / EOS

Patches:

- `patches/0214-fix-api_server-flush-worker-thread-hops-before-assis.patch`: track and await thread hops before terminal events.
- `patches/0228-refactor-api_server-worker-thread-hops-in-api_server.patch`: the hop tracker moves to `api_server_thread_hops.py`; EOS via `call_later`, no dangling task.

## Behavior

Under load (about 1 in 7000 turns on CPython 3.14.7) the last `assistant.commentary` (or delta/tool) event was dropped from the
SSE stream: `call_soon_threadsafe` issued by the executor thread could run after the event loop already saw
`run_in_executor` finish, so `assistant.completed`/EOS overtook it and the writer never sent it. Now terminal events wait for
every pending hop.

## How it works

`gateway/platforms/api_server_thread_hops.py` tracks `call_soon_threadsafe` hops from worker threads (`_hop`), offers
`flush_hops(timeout=5.0)` (bounded wait until every hop scheduled so far ran) and `call_when_flushed(loop, fn, ...)` (re-checks
with `call_later` rather than spawning an unreferenced task), plus a queue class whose `put_threadsafe` pushes via a hop and
`put_after_hops` enqueues once hops landed. Writers `await flush_hops()` after the worker is done and before enqueueing EOS.

## Code

- `gateway/platforms/api_server.py`, `gateway/platforms/api_server_openai_routes.py`, `gateway/platforms/api_server_thread_hops.py`.

## Tests

- `tests/gateway/test_api_server_thread_hops.py`: late hops are flushed before the terminal event; EOS ordering. Not re-run when this doc was written (untested here); the 1/7000 race itself is not reproduced by a test.

## Applying

`git am -3 --empty=keep` in order 0214, 0228.

## Gotchas

- The flush is bounded (5 s): a stuck hop cannot hang the stream forever, at the cost of the old drop.

## Plugin?

No.
