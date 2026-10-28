# Runners Desktop — machines and slots

Status-bar chip (order 6) and panel. Two groups, one row per machine, one square per slot:

- **Gateway workers**: machines that run Kanban card runs, from the Board machine registry. Slots are the
  registry's `max_card_runs` (this gateway falls back to its own `kanban.max_in_progress`; otherwise
  "slots not set" and only the busy count). Busy slots are live Board leases from the gateway's sync-shadow
  board replicas: hover shows card id, title, board and run time; click opens the card.
- **CI workers**: GitHub Actions runners from the runners collector, grouped into machines by runner name
  (`ec2-gwgen-2` → `ec2-gwgen`, `…-i-<instance>` → one ephemeral group). One runner = one slot. Deregistered
  runners are history: only their count is shown. A failed readiness check is a ⚠ line, not a red slot.

Slot styles: filled = busy, outline = free, dotted = offline/unknown, hatched = draining (registry `hold`),
red = lost (machine holds cards but the Board has not seen it for 10 min). An asleep machine with nothing
running is grey with "not seen for N min". A section the gateway cannot read says Unknown/Stale with the reason.

## Data and load

`GET /api/plugins/runners/v1/fleet` (plugins/runners/dashboard/plugin_api.py, logic in plugins/runners/fleet.py).
The server builds one result per 5 s per profile, single-flight, so any number of open panels cost one
registry read (core caches it 60 s), indexed reads of each board replica (~30 ms on the hub) and one
inventory projection. A registry older than 3 min marks gateway liveness unknown (Board not answering).

The panel polls every 15 s, open or closed (so opening it always shows data at most 15 s old), and not at
all while the window is hidden. One poll
runs at a time; each request times out after 5 s. The React Query cache keeps the last view during refetches,
so an open panel updates in place. A failing gateway is retried after 15 s, 30 s, 60 s … capped at 5 min, and
its last data stays on screen marked "Stale: last update … · <error>". Each section comes from the connected
gateway with the best view of it (ready > stale > unknown).

## Tests

```sh
npm ci --prefix desktop-plugins/runners/tests --ignore-scripts --no-audit --no-fund
node --test desktop-plugins/runners/tests/*.test.mjs
python -m pytest plugins/runners/tests/test_fleet.py plugins/runners/tests/test_api.py
```

- registry → slots, pinned only, slots not set, draining: `fleet.test.mjs` "registry", `test_fleet.py`
  `test_registry_sets_slots_flags_and_drain`
- live leases → busy slots with card titles, click opens card: "live leases", `test_live_leases_fill_slots_across_boards`
- CI runners → machines, offline, warnings, hidden history: "CI runners", `test_ci_runners_grouped_into_machines_with_one_slot_each`
- unreachable → backoff, stale last data, never red: "unreachable", `test_unreachable_registry_and_unseen_machines_are_unknown_not_failed`,
  `test_stale_registry_keeps_slots_but_not_liveness`
- REST route, section isolation, 5 s server cache: `test_api.py` `test_fleet_route_*`
- open panel updates in place, one request per poll, no polling while hidden: "open panel updates in place"

Not covered: native Desktop rendering (checked by hand after hermes-sync).
