# Latency — Ledger Desktop

Status-bar chip and the approved Ledger panel. Requires the `latency` backend
plugin and a registered hub connection (the same `hermes-hub` / hub-labelled
connection selection as hub-status). Read/write requests explicitly target that
connection's default profile, not whichever chat is focused.

- Chip: eight calendar days of turn-first-reply p90 and the **server's** count of
  metrics over 2× target in the last 24h.
- Hover or focus opens the five worst metrics **upward**, with collision bounds.
  Click the chip or a worst-metric row to open the full panel; Esc closes.
- Panel: Using Hermes / Cards & workers / Ship, p50/p90, target and eight-day
  history, selected-range chart, available stages and slow samples. Ranges are
  24h / 7d / 30d / 90d. Missing stays `not measured yet`, never zero.
- Black/monochrome and cyan match approved Ledger A. Red/amber/green dots encode
  >2× target / above target / within target; no samples has an outlined dot.
- Initial `GET /v1/ledger`; changed-only `/v1/events` pushes when the active
  connection is the hub and core supports its socket. Otherwise, explicitly
  labelled **Not live**, with the shared React Query 30-second fallback. No
  quantities/counts/percentiles are derived in the renderer.

## UI span transport

Listens for renderer-local SDK event `desktop.ui_latency`, whose payload is
`{name,start_ms,duration_ms,session_id?,card_id?,profile?,board?,gateway?,trace_id?,parent_span_id?}`.
The accurate visual endpoint probes belong in core; the plugin does **not** infer
paint from a REST response or a focused-session atom change. Session switches end
only after the matching runtime transcript commits visibly and crosses two animation
frames; new sessions require the matching visible editable composer. Superseded,
hidden, failed or >15-second attempts are dropped. Async completions carry an
attempt token, so an older request cannot finish a newer click.

`user-steps` uses the same optional SDK controls for `ui.decision_submit`: an actual
user submission ends when its decision POST returns an operation recording receipt,
not when downstream agent delivery finishes. Retries/recovery polls are not new
click samples. Old core without these controls continues submitting normally.
Neither request/answer text nor choice labels enter the timing payload.

Web Crypto makes valid trace/span IDs with no OTel SDK dependency. Only metric IDs,
clock values and safe identifier attributes are serialized. Batches of at most
100 go to `POST /v1/spans` every 5 seconds and on reconnect. The plugin-namespaced
store buffers at most 500 events for less than one hour and retries exact IDs.
The hub validates the whole batch before writing; retries are replay-safe.

## Tests

On a pool worker:

```sh
cd desktop-plugins/latency/tests
npm ci --ignore-scripts
npx playwright install chromium
npm test
```

Uses real Chromium, React, Radix and React Query with a **fixture API**; checks
server-owned chip count and eight-point spark, hover/focus and upward/window
bounds, expansion with target/stages, empty-state rows, all ranges, 640px panel
no-overflow, qualified session navigation, valid IDs, privacy and offline
retention/drop. Optional `LATENCY_EVIDENCE_DIR` saves the fixture screenshot.
Fixture evidence does not establish deployment, core instrumentation or MBP live
acceptance; those gates must be checked separately.
