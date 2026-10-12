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
