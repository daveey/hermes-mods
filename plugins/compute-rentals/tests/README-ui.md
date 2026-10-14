# Compute Rentals desktop UI verification

## Staging and dependencies

- Runtime entry: `../desktop/plugin.js` (plain JavaScript ESM; no JSX/build output).
- Runtime imports are **only** `@hermes/plugin-sdk` and `react`; desktop directory contains only `plugin.js`.
- Default export is opt-in (`defaultEnabled: false`). This task did not copy anything into `desktop-plugins`, change configuration, enable the plugin, or restart the app.
- Tests reuse the existing Hermes checkout's React, React DOM, React Query, Radix UI, and jsdom. No npm install or new dependency manifest is needed. `HERMES_DESKTOP_PACKAGE` can point to another existing checkout's root `package.json` for module resolution.

## Run

```sh
/opt/homebrew/bin/node --test plugins/compute-rentals/tests/plugin.test.mjs   # from the hermes-mods root
```

Node 26 is used because the installed checkout's jsdom dependencies are not compatible with the default Node 18. The harness mounts the actual exported component with real React, React Query, and Radix Popover. Host atoms/REST are isolated fixtures. The themed Button/StatusDot wrappers and portal placement are simplified. This is **not** a screenshot or live Electron test.

18 tests cover exact triple ownership, durable IDs, deduplication, exact decimal currency aggregation, unknown/partial totals, dynamic providers (RunPod, Modal, Prime Intellect, Slurm, backend-labelled extra providers), status dots, provider/dot/summary clicks, native Escape, Session reset, polling keys/cadence, stale callbacks and late responses, cross-gateway fail-closed behavior, details/coverage/errors, refresh, registry link/unlink and readback verification, failed/unapplied writes, in-flight write focus changes, draft sessions, and safe console/clipboard behavior.

TDD tracer bullets were run red before implementing ownership, totals, the first UI path, metadata/refresh, binding, stale-query prevention, unresolved-identity handling, and the native summary-click regression. Later tests exercise already-built edge-case safeguards.

## Identity and backend integration

- Read `host.state.focusedStoredSessionId` (never runtime ID), `focusedSessionOwner`, `connectionId`, and `profile`.
- Query key includes ambient connection/profile plus focused owner connection/profile and durable session ID. Identity-keyed component mounts clear popover selection and scope. Every query and write checks current host atoms before dispatch; post-write refresh is skipped after focus changes.
- `ctx.rest` has no explicit connection/profile route override in this SDK. Focus on another connection fails closed rather than contacting the wrong gateway.
- GET `/inventory` sends `gateway_id=` empty. The backend must resolve and return the canonical **top-level `gateway_id`**. Client `connectionId` is never substituted for the server UUID. Missing canonical identity gives an unknown Session summary and disabled linking, not a false zero.
- Backend integration must provide TTL auto-refresh for `refresh=false` (coordinator-owned); regular `useQuery` polling is every 30 seconds. Manual refresh and write verification set `refresh=true`.
- POST/DELETE `/bindings` explicitly sends `{machine_key,gateway_id,profile,session_id}`. These are registry-only actions; no cloud lifecycle routes/buttons exist. A fresh inventory must confirm the exact binding before the UI treats it as changed.

## Placement acceptance remains with coordinator

Source inspected: `apps/desktop/src/app/shell/hooks/use-statusbar-items.tsx` builds the left cluster as `[...coreLeftStatusbarItems, ...extraLeftItems]`; `gateway-health` is one of the earlier core items. Contribution `order` sorts contributions, not core items. This implementation uses `statusBar.left`, order `-1000` (first contributed slot), without patching core or manipulating its DOM. **Immediate adjacency to gateway-health is not yet satisfied**; the coordinator is investigating supported ordering preferences or a core extension and must verify it before claiming final acceptance.

## Live verification still required (do not restart automatically)

After the coordinator integrates and the user chooses to enable the unified plugin:

1. Verify statusbar adjacency in the running desktop and no plugin error toast.
2. Verify actual theme/layout, especially long fleet dot groups and multi-currency summaries at narrow widths.
3. Switch focused sessions, profiles and gateways; confirm identity gating and Session default.
4. Open vendor, machine and summary targets; inspect metadata, coverage, stale/error states, console and copied SSH.
5. Verify registry link/unlink against the real backend inventory without changing cloud machines.

The live application's CDP endpoint was reported unavailable; this task did not restart or enable the app to obtain it.
