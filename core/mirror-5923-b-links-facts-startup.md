# In-process links display facts selected at serve startup

Mirror snapshot 5923; ordinals 5888, 5889.

Entries:

- **5888**: feat(links): select in-process display facts at serve startup
- **5889**: test(links): share one serve lifespan across auth burst clients

Builds on [shared facts core](mirror-5923-b-shared-facts-core.md) and the earlier remote-links display refresher.

## Behavior

- The legacy stale-display refresher re-probed the same origin stores on every view poll. At serve/dashboard app startup the launch origin's legacy refresher is now fenced and drained, the canonical links projection is captured through a change feed into an in-process fact store, and stale-tolerant display readers are routed to that immutable publication.
- `HERMES_FACTS_LINKS` is ON when unset; `0`, `off`, `false` or `no` in the launch environment resumes the legacy refresher on the next natural startup, closing any retained replacement first.
- Only the exact launch origin scope is authorized; other profile/storage scopes, explicit request bindings, and strict/read-your-writes callers keep their existing path. External input is recaptured at most every 30 s; the capture shares the strict projection lock and charges its wait to a five-second capture budget; a failed capture publishes unknown/stale, never a successful empty inventory; the existing 90 s validity is checked on every display read; a dead feed is unavailable. Display requests never fall back to synchronous projection or legacy admission.
- Shutdown closes the feed and its captured worker before removing the binding and resuming legacy admission. Failed cleanup retains the startup owner and fence and blocks a replacement; a drain timeout retains its fence. No replica IPC, demand channel or per-request watch is started.
- 5889 changes only a test: the auth-burst singleflight test now models the real topology (one app lifespan, four separate HTTP clients with their own cookie jars) instead of four test-client contexts competing for one exact-origin owner.

## How it works

- `hermes_cli/fact_links_startup.py` (new): module-level registry of per-origin `LinksRuntime` objects, `binding_for(origin)` memory-only lookup, an async lifespan context that drains legacy, starts the owner and releases after services stop.
- `hermes_cli/fact_remote_links.py`: capture projection takes the strict projection lock and accounts the wait against the budget; serialization of published rows.
- `hermes_cli/kanban_session_activity.py` selects the binding for `stale_ok=True` display entry points; `hermes_cli/web_server_app.py` composes the lifespan. Design record: `docs/designs/links-facts-cutover.md`.

## Code

Shipped non-test paths:

- `docs/designs/links-facts-cutover.md`
- `hermes_cli/fact_links_startup.py` (new)
- `hermes_cli/fact_remote_links.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/web_server_app.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_fact_links_startup.py` (serve lifespan defaults on and reads the publication; switch off resumes legacy and preserves strict and display paths; bound, strict and foreign scopes stay legacy; drain timeout keeps the fence; real app composition binds before services and releases after; failed cleanup retains fence and binding and blocks a duplicate)
- `tests/hermes_cli/test_fact_remote_links_serialization.py` (capture waits for a strict reader and holds the lock; lock wait past budget fails without projecting)
- `tests/hermes_cli/test_refresh_singleflight.py` (5889: one serve lifespan shared across auth burst clients)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- The design doc states live CPU acceptance is separate: a unit-test pass or merge does not establish it; it needs a sustained per-thread context-switch sample after deployment.
- Rollback is an environment change plus a restart; no live process is signalled by the cutover.
- Display freshness is bounded by the 30 s recapture, so this path must not serve read-your-writes callers.

## Plugin

Core change; no plugin or hook is involved.
