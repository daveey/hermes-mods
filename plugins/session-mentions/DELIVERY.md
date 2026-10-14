# Session send transport

Approved scope: repair the plugin transport; keep core ownership checks and do not restart existing runtimes.

Plan: use the current Desktop gateway's owning socket for a matching connection/profile (not a separately pooled backend); use exact profile routes otherwise. Resume via that socket, submit with queued=true, distinguish queued admission from started delivery, never retry an uncertain submit, and correlate replies to the accepted user row only. Files: desktop plugin.js, plugin-local tests and this record. No core changes.

Acceptance: regression tests for owner routing, busy queue, profile mismatch, rejected and uncertain sends; real live throwaway recipient, different sending socket, unchanged ownership lease, exact message in persisted transcript. Unsupported independent owners must retain the ownership refusal, not be taken over.

Investigation: shared_session_attach only discovers advertised shared_runtime_url; current shipped serve runtimes do not advertise/serve that handshake. bot_live_delivery's poller only accepts the canonical Bot Chat session, so it cannot safely deliver arbitrary sessions. Do not repurpose it by changing session titles or disabling leases.

## Verified

- `node --test ~/.hermes/plugins/session-mentions/tests/delivery.test.mjs`: six passing regression tests (owner primary route; null-primary resolution; exact profile refusal; submit status; unavailable correlation; uncertain submit/no retry). The original owner-route test failed with SESSION_NOT_OWNED before the change.
- `SM_LIVE_URL=http://127.0.0.1:<backend-port> node ~/.hermes/plugins/session-mentions/tests/live-delivery.mjs`: creates a new throwaway session in the running local backend and uses two independent sockets. No restarts or working-session sends. Latest proof is tests/live-evidence.json.
- Real busy delivery returned queued=true/delivered=false, then produced exactly one attributed user message and the expected model reply. Idle delivery returned the correlated model reply. Recipient PID, lease id, and runtime id stayed unchanged. Only the throwaway session was closed afterward.
- Core git checkout remains clean.

## Limitations / not claimed complete

This repairs use of the currently connected owning Desktop backend. It does NOT discover arbitrary independent CLI/serve processes that are not reachable through that connection's RPC route; those still safely refuse SESSION_NOT_OWNED. No cross-process generic mailbox was installed, and the live test is two sockets into one existing owner backend, not a second independent owner process. Remote owner routing was not live-tested in this change. Packaged Electron hot-load/render was not directly inspected.

Busy queue acknowledgements currently omit user_row_id, so wait_for_reply returns an explicit unavailable-correlation note for queued sends instead of claiming an old reply. Streaming submits with a returned row id support correlated waits.

Desktop admission lint still flags the existing doubled-prefix MutationObserver's document.body reach; no new finding was introduced by the transport fix.

## Home courier (fix: send fails when the target chat is open in a window)

Root cause: every Desktop with this plugin drains the outbox of every gateway it is connected to. Another Mac's
Desktop reaches this gateway through a separate SSH-isolated `serve` process, and core refuses a submit from there
into a chat this Mac's Desktop holds open ("This chat is open in another Hermes window/terminal"). Whichever courier
polled first won, so sends failed at random (state.db shows the failures with `gateway: "macbook"`, i.e. the mini's
Desktop draining the MacBook).

Fix:
- Each Desktop's courier is the "home courier" of its own machine (`kind: local` connection): it heartbeats
  `POST /result {id: "home-courier"}` every 5s and drains with `?home=1`. While that heartbeat is <20s old, any other
  drain of that outbox returns nothing (`relay_files.claim_pending(home=False)`).
- A send into ANOTHER gateway first asks it (`GET /ping` -> `home_courier`); if it has a live home courier the send is
  forwarded (`POST /enqueue`, poll `GET /status/<id>`), so that gateway's own Desktop delivers it through the owning
  socket. No home courier, an older backend, or a request unclaimed after 20s (withdrawn via `POST /cancel`) -> direct
  send as before. A lost enqueue ack only falls back once the request is provably absent.
- A forwarded send refused over there falls back to the direct route: the chat may be open in THIS Desktop, whose own
  backend on that gateway owns it. The courier ticks from a dedicated worker, because Chromium throttles an idle
  hidden window's timers to ~1/min (the mini's heartbeat went stale and requests waited a minute).
- Core's ownership refusal is reported as `status: "refused", delivered: false, retry_safe: true` (the turn was never
  admitted) instead of "unknown".

Deploying: the Desktop half hot-reloads on save. The dashboard routes are mounted once per backend, so a backend keeps
the old outbox until it restarts. The gate only needs the NEW code in the remote-facing (SSH-isolated) backends another
Mac's Desktop spawned here; they are respawned on demand, so restart them when idle (no live `session_turn_leases`).

Tests: `uv run --with pytest python -m pytest -q -p no:cacheprovider tests/test_relay_files.py` and
`node --test tests/` (courier.test.mjs). Live: `tests/live-remote-drain.mjs` (plays the other Mac's courier against
the real SSH-isolated backend; before: claimed + refused, after: not claimed, home courier delivers) and
`tests/live-home-courier.mjs` (N real tool sends into a throwaway chat owned by this Mac's Desktop).

Live results (2026-10-13): `live-remote-drain.mjs` against the mini Desktop's SSH-isolated backend on the MacBook,
before the fix: claimed by the remote drain, submit refused "This chat is open in another Hermes window", nothing
landed; after restarting that backend: not claimed, the MacBook's home courier delivered it (`this-device`, queued,
landed, owner lease unchanged). `live-home-courier.mjs 4`: 4/4 landed. Cross-gateway: a send from the MacBook into a
throwaway chat owned by the mini's Desktop was forwarded (`forwarded: true`, streaming) and landed in 4s, owner lease
unchanged (`live-forward-owner.mjs` on the mini).

Limitation: a chat on this gateway that is open in ANOTHER Mac's Desktop (owned by that Desktop's SSH-isolated backend
here) is still refused to the home courier; that is reported as `refused` (safe to retry) and the chat's "Take over
here" applies.
