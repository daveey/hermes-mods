# Runners Desktop — machines and slots

Status-bar chip (order 6) and panel. The chip shows two bars, **Hub** (the hub's card slots) and **Workers**
(every other gateway with a slot cap): the track is capacity, the fill is scheduled cards; green while there is
room, red once every slot is taken. A status dot appears only for warn/bad (stale data, lost machine).

The panel has two groups, one row per machine, one square per slot:

- **Gateway workers**: machines that run Kanban card runs, from the Board machine registry. Slots are the
  registry's `max_card_runs` (this gateway falls back to its own `kanban.max_in_progress`; otherwise
  "slots not set" and only the busy count). Busy slots are live Board leases from the gateway's sync-shadow
  board replicas: hover shows card id, title, board and run time; click opens the card. The section header adds
  `· N waiting`: unleased ready cards assigned to profiles the machine-wide gateway dispatcher can spawn
  (existing profiles permitted by `kanban.dispatch_profiles`, evaluated under the Kanban home, not the UI profile).
  Each row splits this queue into `N waiting` (placement fits here) and `N can't run here` (labels, including
  skill labels, machine pins, creator/workspace restrictions, pinned-only policy, or another gateway's
  default-placement card that a machine with `explicit` pickup never takes). Hover either number
  for its meaning. Portable cards may fit multiple rows but count once in the header. Availability, holds,
  quotas and transient admission limits are separate: a fit does not promise immediate dispatch.
  The projection assumes the shared board's machine-wide dispatcher policy; it does not inventory remote
  profile configs. Older gateways and unreadable replica scans show no waiting count, never a false 0.
- **CI workers**: GitHub Actions runners from the runners collector, grouped into machines by runner name
  (`ec2-gwgen-2` → `ec2-gwgen`, `…-i-<instance>` → one ephemeral group). One runner = one slot. Each slot
  carries the runner entity id (`runner_…`) from the fleet projection. Click or hover a slot to open its
  popover: **Repair in new session** (or **Open repair session** when that runner already has an active
  repair). The action is enabled only when that gateway's enrollment has `features.repair_sessions` and the
  runner has an open incident, or an active repair to reopen. It uses the captured owner route: idempotency
  keys, owner/epoch checks, status reconciliation, and no prompt resend. Deregistered
  runners are history: only their count is shown. A failed readiness check is a ⚠ line, not a red slot.

CI groups: CI machines are grouped by category (`fleet.category` on the gateway; the panel falls back to the same
rule for older gateways): **Macs** (macOS label or `mac` in the name), **EC2 Linux** (`ec2-*`, `hermes-hub*`,
other Linux), **Metta** (`metta*`), **Ephemeral** (per-instance runners), **Other**. Each group is one collapsed
row: `▸ ● EC2 Linux (9) · 2 offline · ⚠ 1 … 5/14` (slots busy/total; dot red = a readiness warning, amber = some
offline, green = all fine). Click to expand its machines as before; which groups are open is remembered in
localStorage (`hermes-runners-ci-open`). Gateway workers stay a flat list. Each gateway machine is one line under a column header
(Machine, Slots, Waiting, Can't run, CPU, RAM, Disk free, Busy): rows hold bare numbers and bars, the
header carries the unit, and all rows share one column grid. Flags and reasons (pinned only, slots not set, on hold,
not seen, load N min old, load not reported) are hover text on a small ⓘ after the machine name,
shown only on machines that have any. A stale load greys its cells and an unreported one shows "–". The panel is up to 680 px wide.

Load: each gateway machine reports CPU load, RAM in use and disk space to the Board every 30 s (core
`hermes_cli/machine_metrics.py` → `POST /v2/machines/me/metrics`, returned by `GET /v2/machines`). The row
shows a bar and a number under the CPU, RAM and Disk free headers (CPU = 1-min load ÷ cores; a bar at 90 % or more is
orange), "–" (hover: "load not reported") until the machine's core has the reporter, and grey cells (hover: "load N min old") after 2 min.
CI machines show load and free disk only when the runners collector probes their host (no RAM there); the
others have no meter line.

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
- CI runners → machines, offline, warnings, hidden history, and the runner entity id on each slot: "CI runners",
  `test_ci_runners_grouped_into_machines_with_one_slot_each`
- repair popover → create a session, reopen the same session id twice, disabled without an incident:
  `repair.test.mjs`
- unreachable → backoff, stale last data, never red: "unreachable", `test_unreachable_registry_and_unseen_machines_are_unknown_not_failed`,
  `test_stale_registry_keeps_slots_but_not_liveness`
- REST route, section isolation, 5 s server cache: `test_api.py` `test_fleet_route_*`
- load meters reported / absent / stale, CI host probes: "load meters", `test_gateway_load_reported_absent_and_stale`,
  `test_ci_machines_take_load_from_collector_host_probes` (reporter and Board route: core
  `tests/hermes_cli/test_machine_metrics.py`, hermes-hub `worker/test/machines.test.ts`)
- open panel updates in place, one request per poll, no polling while hidden: "open panel updates in place"

- CI groups: categories + counts + health, collapsed by default, expand/remember/collapse: `groups.test.mjs`,
  `test_fleet.py` `test_ci_machine_categories_for_the_panel_groups`
- gateway rows are single grid lines under one column header (bare numbers, units in the header; flags and load age as hover text on an (i) marker only), no Investigate control: "gateway rows: one grid line", "waiting: header counts", "load meters"

Not covered: native Desktop rendering (checked by hand after hermes-sync).
