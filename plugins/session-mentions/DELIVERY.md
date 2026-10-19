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

## Named profiles on a cloud gateway, and naming a Mac (fix: wrong profile; ambiguous `this-device`)

Bug 1, wrong profile. `cross_session send @session:alpha-follow@nous-1/<id>` from the MacBook answered `delivered: true`,
but the turn ran in nous-1's DEFAULT profile: `session.resume` found nothing in `/opt/data/state.db`, opened a stray
root session there, and the reply said the session had no `slack_post` tool. A cloud gateway serves every profile
from one backend (app-global remote mode), and an RPC that names no `profile` runs in the backend's launch profile.
`deliver()` passed the route to `host.requestProfile` but sent `session.resume` / `prompt.submit` without `profile`.
The primary-socket path already added it, which is why typing into the Inbox card worked.
Fix: routed RPCs always carry `profile: route.targetProfile` (the backend's name for it, as hermes-bots'
`scopedBotParams` does).

Bug 2, ambiguous `this-device`. Every Desktop connected to a gateway drains its outbox, and every Desktop calls its own
Mac "This device", so `@session:default@this-device/<id>` sent from nous-1 resolved on whichever Desktop claimed it
first (MacBook sessions came back `404 Session not found` from the mini). Fix:
- Each Mac has a device name: backend config `session_mentions.device_name` (`macbook` on the MacBook, `mac-mini` on
  the mini, set per host, the names the other Mac's registry already uses), else the short hostname, which stays an
  alias. `GET /ping` returns `device` + `aliases`. A backend still serving the old routes has no `device` in `/ping`, so
  the Desktop falls back to `/api/system/stats` `hostname`.
- The Desktop keys its own `local` connection by that name, so refs it hands to other gateways read
  `@session:<p>@macbook/<id>`. `this-device`, `local`, the hostname and the device name all still resolve locally.
- The tool stamps `route` (the gateway part of the target, or `gateway` for list) on each request. Couriers drain
  with `courier=<device>&aliases=…&reach=<every gateway name they resolve>` and are registered in
  `session_mentions/couriers/<device>.json`. Claim rule (`relay_files.may_claim`): no route means anyone. A route of
  `this-device`/`local` goes to the gateway's own home courier only. A route naming a live courier's device goes to
  that courier only, even when another Desktop reaches that Mac through a registry connection. Otherwise it goes
  to a courier whose `reach` has the route. A legacy drain (no `courier`) still claims everything else.
- The tool fails fast on `this-device` from a gateway with no home courier ("ambiguous … name the Mac"). A route no
  live courier can reach times out with "No connected Hermes Desktop that can reach `<route>`". Both errors list the
  Desktops draining right now.
- The dashboard routes now pass every query parameter through and re-exec `relay_files.py` whenever its mtime
  changes. Routes are mounted once per backend, so this change needed one restart per backend; later relay_files
  fixes don't.

Tests: `node --test tests/targeting.test.mjs` (profile on routed resume + submit; targetProfile vs label; device keying,
aliases, collision; drain query; ping and hostname fallback) and `uv run --with pytest --with fastapi --with httpx
python -m pytest -q -p no:cacheprovider tests/test_targeting.py` (route parsing; device-owned, this-device, reach,
dead-owner and legacy claims; tool route stamp and fast-fail; routes end to end). Without the fix, all 6 node tests
and 17 of the pytest tests fail.

Live results (2026-10-19 PDT):
- Profile. From a MacBook session: `cross_session send @session:alpha-follow@nous-1/<throwaway alpha-follow session>`
  with wait 120 returned `replied`, `user_row_id: 151`. Rows 151–152 landed in `/opt/data/profiles/alpha-follow/state.db`.
  `/opt/data/state.db` stayed at max id 658 with no session of that id. The throwaway session was deleted afterwards.
- Targeting. nous-1's dashboard was restarted onto the new routes (`s6-svc -r /run/service/dashboard`, no turns in the
  previous 10 min). Both Desktops then drained nous-1 as couriers `david-mbp` and `daveey-mini`. Their backends still
  ran the old routes, so the names came from the hostname fallback. From nous-1, `cross_session read` gave:
  `@session:default@david-mbp/20261019_132933_0b8012` (the session that used to 404) was answered by the MacBook
  4/4 times (`gateway: david-mbp`, ~2.5 s). `@session:default@daveey-mini/<id>` was answered by the mini 4/4 times
  (`gateway: daveey-mini`). `@this-device/...` failed fast with the "ambiguous" error and listed both Desktops.
  `@macbook/...` was served by the mini through its `macbook` SSH connection. The MacBook answers it itself once
  its backend restarts and reports `device: macbook`.

