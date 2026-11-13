# Client closes idle keep-alive sockets before the backend does

Patches: **3515**.

## Behavior
- Desktop-to-remote polling no longer flaps with "socket hang up" caused by writing into a pooled connection the backend had already closed.
- The backend's HTTP idle keep-alive timeout rises from uvicorn's 5 s default to 120 s on every listener; Desktop pooled agents close a free socket after 30 s, so the client always closes first.

## How it works
3515 adds `KEEP_ALIVE_IDLE_S = 120` in `web_server_transport.py`, used by `web_server._build_uvicorn_server` and the listener builder in `web_server_lifecycle.py`. In Desktop's `api-transport.ts`, all pooled agents (json, probe, download) come from `keepAliveAgent()` with `CLIENT_KEEPALIVE_IDLE_MS = 30 s`.

## Code
- `apps/desktop/electron/api-transport.ts`
- `apps/desktop/electron/keepalive-race.test.ts`
- `hermes_cli/web_server.py`
- `hermes_cli/web_server_lifecycle.py`
- `hermes_cli/web_server_transport.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/keepalive-race.test.ts`: `an agent that keeps idle sockets forever writes into server-closed sockets (socket hang up)`, `keepAliveAgent closes its idle socket first, so the same polling never races the server close`, `every pool uses the client idle timeout`, `stays well under the backend's KEEP_ALIVE_IDLE_S (hermes_cli/web_server_transport.py)`.
- `tests/hermes_cli/test_web_server_ws_ping.py`: `test_start_server_keeps_idle_http_connections_open_longer_than_desktop_does`, `test_gateway_listener_keeps_idle_http_connections_open_longer_than_desktop_does`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The client idle timeout must stay below the server constant; the Desktop test pins this margin.
- Intermediate proxies or tunnels with their own shorter idle timeouts are not addressed.
