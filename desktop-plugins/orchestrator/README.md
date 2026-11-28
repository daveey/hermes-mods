# Orchestration Console — Operator Flight Deck

Opt-in Desktop route `/orchestrator`, paired with the `orchestrator` backend plugin. It does not replace or delete Command Center or session-panel.

## Install

Link `plugins/orchestrator` into the steering gateway's Hermes plugin directory and enable it with `hermes plugins enable orchestrator`. Link `desktop-plugins/orchestrator` into the Desktop plugin directory, then enable **Orchestration Console** in Settings → Plugins. The backend's configured producer plugins must be installed; missing producers show named **unknown** states rather than fabricated zero counts.

The console discovers the steering connection through the existing lanes authority endpoint. It uses that connection for snapshot reads and separately verifies steering authority before a lane write. Never point writes at a display-only gateway. Remove/disable both halves to reverse the installation; no data migration is involved.

## Data and authority

- `GET /api/plugins/orchestrator/v1/snapshot?q=&lane=` returns `OrchestratorSnapshot` version 1. All totals, filtered totals, shares, gate figures, cost and telemetry are server-owned. Arrays may be partial; the renderer does not substitute array length for totals.
- `WS /api/plugins/orchestrator/v1/stream` sends an initial `{type: 'snapshot', snapshot}` then cursor-bound replacement-field deltas. A mismatched base cursor triggers a REST refresh.
- The current SDK socket is ambient-connection-only. When it cannot safely target the steering connection (including OAuth remotes), the console uses a 15-second query-cache polling fallback and says **not live**. It never silently streams another gateway's data.
- Sources: canonical lanes projector/governor sidecars; existing session-panel process/cache; user-steps ownership requests; session-repair findings; hub-sync; hub-status history; runners fleet/CI occupancy; saved throughput quota runway samples. The console does not open sibling board SQLite files or refresh subscription quotas.
- Session coverage is that reported by the canonical cached/watched session-panel producer, not a new independent transcript scan. A foreign profile feed is refused rather than leaking the launch profile's data.
- Human requests require a current human wait, `wakes.blocks_for_human`, or an open owner/revision-bound user-steps request. Historical `block_kind` is not human authority. Machinery findings and admission gates stay separate.
- Missing board evidence makes affected accounting unknown and names the board. A later healthy snapshot restores it. Stale/unobserved occupancy is not idle.

## Controls

`/` focuses filtering; printable typing outside an editor starts a filter. `↑`/`↓` move the row cursor; `Enter` inspects. `Esc` closes a modal, then the inspector, then clears filtering. `⌘K` opens the object palette; `?` opens shortcuts. Dialogs restore focus. The inspector is absent when unselected and 380px when open (narrow layouts show the inspector instead of squeezing the list).

The lane share and pool-cap number controls commit on blur/Enter and wait for a separate producer readback. Unsupported ceilings stay refused by the lanes backend. Opening a session resolves its owner against registered Desktop routes and passes the full route to `host.openSession`; ambiguous or missing ownership fails closed.

Approval/release choices reuse the exact producer request. Before POST, a durable idempotency key is saved; options lock during execution, and retries reuse that intent. Operation receipt readback is separate from transport success. A **recorded/pending** operation is not claimed delivered or applied. Free-text/revision choices open in the owner session instead of inventing a response in this surface.

## Tests

From repo root:

```sh
scripts/test-all --exact -k plugins/orchestrator
scripts/test-all --no-core --exact -k plugins/orchestrator
cd desktop-plugins/orchestrator/tests
npm ci --no-audit --no-fund
npx playwright install chromium
npm test
```

The backend suite exercises the real core API loader/profile dependency where core is available, canonical producer interfaces, sibling-board failure/recovery, current human classification, wait-chain exclusivity, telemetry source recovery and cursor deltas. The real Chromium suite exercises 1440×900, 900×760 and 640×760 geometry, console errors, CDP key sequences, count authority despite deliberately disagreeing row samples, remote session routing, persist-before-POST decisions and steering readback. CI runs Chromium in its own `orchestrator-browser` job. Browser action tests use a mocked Desktop bridge, not a signed-in production Desktop.
