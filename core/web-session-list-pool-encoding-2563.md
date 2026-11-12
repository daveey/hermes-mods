# Session list responses are JSON-encoded in the pool thread

Patches: **2526, 2527**.

## Behavior

`GET /api/profiles/sessions` (polled by Desktop several times a second, up to 200 rows) returned a dict from its coalesced pool-thread read, so FastAPI's `jsonable_encoder` walked the whole page on the event loop. The same routes now encode in the worker thread. Status, headers and body bytes are unchanged; the patch's own bench reported loop-thread CPU per 200-row request falling from about 8.3 ms to 0.7 ms. Payloads that are not JSON-native keep the old path.

## How it works

- New `hermes_cli/web_json_response.py`: `native_json_response(payload)` builds `JSONResponse(content=payload)` in the calling thread, returning the payload unchanged on `TypeError`/`ValueError` (datetime, set, Path, dataclass, Enum, NaN) so FastAPI encodes it as before, and passing a `Response` through. `encoded_in_thread(func)` wraps a sync read with `functools.wraps`. `per_request_response(result)` gives each request its own constant-time `Response` over the shared rendered body (same status, media type, content-length).
- Why a per-request object (rationale corrected in 2527): FastAPI sets `.background` on a returned Response, so one shared object would carry one request's background tasks into another. The earlier CORS-header rationale did not hold.
- Routes changed on both miss and cache-hit paths: `GET /api/profiles/sessions`, `/api/profiles/sessions/sidebar`, `/api/profiles/projects/tree` (in `web_routers/profiles.py`) and `GET /api/sessions` (in `web_routers/sessions.py`). Direct Python callers still get dicts.

## Code

`hermes_cli/web_json_response.py` (new), `hermes_cli/web_routers/profiles.py`, `hermes_cli/web_routers/sessions.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_web_json_response.py`: byte equality against `JSONResponse(jsonable_encoder(payload))`, non-native payload fallback, `encoded_in_thread` signature, per-route no-`jsonable_encoder` checks over HTTP (httpx ASGI client), body parity, and coalesced requests each sending a distinct Response object (2527). Covered entry path: REST through the ASGI client. Desktop and Webapp clients are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Not changed: `/api/profiles/sessions/stamps`, POST/mutating routes, and routes with injected `Response`, `response_model` or non-200 status.
- A JSON-native payload is rendered with `json.dumps` defaults, matching the previous output for such payloads.

## Plugin

Core change; no plugin required.
