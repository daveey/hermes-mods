# Runners dashboard API

The dashboard mounts `plugin_api.py` at `/api/plugins/runners`:

- `GET /v1/inventory`: cached inventory.
- `GET /v1/runners/{runner_id}?history_limit=100`: cached detail/history.
- `POST /v1/refresh`: validated `RefreshRequest`; queues/coalesces work in the existing collector database. Does not poll providers or start a collector.
- `GET /v1/fleet`: machines and slots (5 s server cache, single flight per profile), plus `feed` (host:pid) and
  `hash` (the fleet's identity, ignoring `generated_at`).
- Push `plugin.runners.fleet` `{feed, epoch, seq, at, profile, hash, fleet | heartbeat | changed}`: while a client
  read `/v1/fleet` in the last 5 min, the serving process (where core's gateway transport is loaded) checks its
  own profile's fleet every 15 s and pushes it when it changed, else a heartbeat every 60 s; JSON-safe, and over
  256 KB only `changed: true` (clients read it). The push thread starts on the first read and exits when interest
  lapses. Tests: `tests/test_fleet_push.py`.

Uses core `_require_token` and verified `request.state.token_authenticated`.
Query tokens and connection identifiers are not credentials. Every supplied Origin
must match scheme, hostname and effective port; malformed and duplicate Origins
are rejected. Cookie/OAuth session refresh requires Origin (including native OAuth
clients, conservatively); verified loopback session tokens and core service-token
principals may omit it. No CORS allowance is added. Core Host validation remains
required when mounted. Reverse proxies must preserve the external request origin
through the core's trusted proxy configuration; forwarded headers are not trusted
independently here.

Only the active runtime home's `runners.yaml` and `runners/state.sqlite` are used.
Its configured owner profile must equal core's canonical active profile; unknown
profiles fail closed. This is a profile fence, not independent gateway-identity
attestation. Collector owner identity/epoch fencing remains the collector's job.
No profile/connection selector from headers or query parameters changes the store.
Missing/unreadable stores return 503 without creating a database. No import-time
config reads, polling, subprocesses or background threads (the fleet push thread starts on a request).

Tests use fictional contract fixtures, temporary SQLite stores and the exact core
auth helper functions extracted without importing/starting the server. They expect
a core checkout at `~/.hermes/hermes-agent`. Full server middleware/discovery and
reverse-proxy/native-client integration require deployment-stage verification.
