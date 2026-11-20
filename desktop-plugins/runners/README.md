# Runners Desktop — machines and slots

Status-bar chip (order 6) and panel. The chip has one 4×4 px pip per card slot in two rows,
with the outlined hub group first and worker machines sorted by name. There are no text labels.
Busy pips are green, red when their hub/worker pool's usable slots are full; free pips are grey.
Throttled slots hatch amber at the end. Draining machines have blue busy pips and faint blue
outlines for leaving capacity; lost machines have red busy pips and a red group outline.
Launching workers pulse amber: pips fill by elapsed boot time / median launch-to-ready time in
the last 24-hour scaler ledger window (34 minutes without completed boots). They use the most
common registered worker slot count (six without registry evidence), but contribute no scheduling
capacity until ready. Registered machines under the scaler's enrolling hold keep the same amber
boot progress (from their ledger launch timestamp), not the blue draining/leaving state. Ordinary
holds remain draining; the popover's existing enrolling label is unchanged. The chip and each group have per-machine tooltips; the accessible name
retains hub/worker totals and names launching workers. Reduced motion disables the pulse.
A status dot appears only for warn/bad (stale data, lost machine). The popover layout is unchanged.

The Runners panel now contains **gateway workers only** (see the independent CI chip below):

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
## CI (GitHub Actions)

A separate **CI** chip (order 7, right of Runners) opens its own panel. Mac then Linux each show a
2-row grid of round 4×4 px pips, one per observed self-hosted runner, and a green count of running
elastic jobs (hidden at zero). Busy pips are green, or red when all ready online runners in that
section are busy. Idle pips are grey, offline pips hollow, and readiness failures get a red ring
and a red status dot. Unknown runners are counted as not observed, never idle capacity. Stale
CI data dims the chip to 45% and shows an amber dot (readiness red takes precedence).
The tooltip names each OS's busy/online count, offline runners, running providers and observation age.

The panel lists **Mac**, then **Linux**: online self-hosted rows, one row per elastic provider with
busy counts by size, then a muted offline/unobserved line. Finished elastic registrations and
deregistered runners appear only as hidden counts. Provider identity and OS come from the fleet
projection's `os`, `provider`, and `size` fields; Blacksmith, Namespace and ephemeral hub CI VMs
never contribute fixed-capacity pips. Older gateway responses use a compatibility fallback.

Each runner retains its entity id (`runner_…`). Click or hover its pip (or its name in the offline
line) for **Repair in new session** / **Open repair session**. The captured owner route, enrollment
`features.repair_sessions`, incident checks, idempotency and epoch fencing are unchanged.
**Show CI** opens this panel; **Show runners** opens gateway workers only. Both chips observe the
same query cache, with Runners owning the single fleet polling interval.

Gateway workers stay a flat list. Each gateway machine is one line under a column header
(Machine, Slots, Waiting, Can't run, CPU, RAM, Disk free, Busy): rows hold bare numbers and bars, the
header carries the unit, and all rows share one column grid. Flags and reasons (pinned only, slots not set, on hold,
not seen, load N min old, load not reported) are a tooltip on a small ⓘ at the right end of the row,
shown only on machines that have any (drawn by CSS: native `title` tooltips do not appear in the Desktop popover). A stale load greys its cells and an unreported one shows "–". The panel is as wide as this table (at least 680 px, at most the window) and reserves a gutter for its scrollbar.

Load: each gateway machine reports CPU load, RAM in use and disk space to the Board every 30 s (core
`hermes_cli/machine_metrics.py` → `POST /v2/machines/me/metrics`, returned by `GET /v2/machines`). The row
shows a bar and a number under the CPU, RAM and Disk free headers (CPU = 1-min load ÷ cores; a bar at 90 % or more is
orange), "–" (hover: "load not reported") until the machine's core has the reporter, and grey cells (hover: "load N min old") after 2 min.
CI's panel is an activity/readiness view; host load telemetry remains in the backend but is not drawn there.

Slot styles: filled = busy, outline = free, dotted = offline/unknown, hatched = draining (registry `hold`; a held machine with nothing running that the Board has not seen for 10 min is "stopped": it has no row and no slots in any total, only its name in the "N off: ..." line under the grid),
red = lost (machine holds cards but the Board has not seen it for 10 min). An asleep machine with nothing
running is grey with "not seen for N min". A section the gateway cannot read says Unknown/Stale with the reason.

## Token throttle and crossed-out slots

With the throughput plugin on the gateway, the panel shows its token throttle above the gateway table: the same
0-100 % level as the Throughput panel (`GET`/`PUT /api/plugins/throughput/v1/throttle`), the cap, the fleet rate
any subscription pool the pacing governor has paced or held, and how many card starts it held in the last
5 minutes. Pacing is text only: the governor's per-machine limits are not published, so no slot is crossed out
for it. Dragging previews; releasing sets the level.

Each gateway row then crosses out the slots a limit will not let run. The gateway answers with, per machine,
its token cost per slot and `allowed`: the slots its share of the token cap pays for
(`plugins/throughput/slots.py`). Slots are drawn busy first, then
free, and every slot past `allowed` gets an X: a free one "cannot start", a busy one "over the limit". The
reason is the slot's hover text and a line in the row's ⓘ. The **Tok/slot** column is in+out tokens per busy
slot per minute over the last 15 minutes (grey when it is the fleet median because the machine ran too little
to measure, "–" when unknown). A machine without a slot cap has nothing to cross out. The gateway serving the
panel shows the slot count its start gate enforces (`enforced: true`, from `plugins/throughput/slotgate.py`);
other gateways' counts are advisory estimates and their hover text says so.

Above the grid, **Token cap** is a person's cap for the whole fleet in thousands of tokens per minute (`150`,
`1.5m`): Enter or leaving the field sends `PUT … {"cap_per_min": N}` once, an unchanged or unreadable value sends
nothing, **No cap** sends `null` (the subscription budget alone then caps). The line beside it says what caps
the fleet (`Cap 60k tok/min (yours)` / `(budget)`, `No token cap`, `Cap 0 · holding every call`), the budget
when it differs, the fleet rate, this gateway's open slots (`here 4/6 slots at 3k/slot`) and pacing.

A machine the autoscaler holds while it enrolls (hold reason `worker-scaler: enrolling …`) is labelled
"enrolling", not "draining".

The chip hatches the final throttled pips per machine; busy pips turn red once the pool's
usable slots are taken, even if throttled ones are free.

Without the throughput plugin, with an older one that sends no `slots`, or when that request fails, there is no
cap field, no column value and nothing is crossed out.

Tests (`tests/throttle.test.mjs`): the limit maths, a rendered panel with a token limit, the panel without
throttle data, the cap field's single commit, refusal and No cap, the cap line, the estimate marker, the
"enrolling" label, and the chip summary. **Not tested**: the request to the
throughput API from a live Desktop (the tests pass the payload in), and the X drawing itself (CSS).

## Worker lifecycle timeline

Under the gateway machine grid (above the "N off" line) a one-row timeline shows what the worker autoscaler did
in the last 24 hours: the axis ends now (left is older) with local hour labels every 6 h, and each event is one
tick at its time: yellow = launched, green = ready for scheduling, blue = draining, red = removed, with a legend
under the axis. Hovering a tick shows `worker · what happened · reason · local time`, for example
`worker-3 · draining · consolidating spare capacity (4 cards finishing) · 03:41`; the same text is the tick's
accessible label, so the kind never depends on colour alone.

A worker keeps one lane (of up to 4) for the whole window, so its life reads left to right. Workers are placed in
order of their first event, then name, each in the lane where fewest of its ticks would land within ~5 px of a
tick already there: workers acting at the same moment stack instead of covering each other. Ticks of one lane
that are still closer than that (a worker launched and ready within minutes, or more simultaneous workers than
lanes) are drawn that far apart, later ones moved right (pulled back from the right edge), so every tick keeps
its own hover target; a nudged tick's hover text still carries the true time. The result depends only on the
events, not on their order.

Data: `gateways.events` in the fleet response, `[{at, worker, kind, reason, instance}]` oldest first (`at` in
epoch seconds, `kind` one of `launched`, `ready`, `draining`, `removed`, `instance` a string or null), plus
`gateways.events_since`, the window start in epoch seconds (now − 24 h). The gateway reads them read-only from the
autoscaler's sqlite ledger (`~/.hermes/state/worker-scaler.db`, or `HERMES_WORKER_SCALER_LEDGER`; table
`events(id, at, slot, iid, kind, reason)`): at most the newest 500 of the window, unknown kinds and malformed
rows left out. A host without that file or table, or with the ledger locked or unreadable, answers `events: []`
(the read waits under a second and never creates or writes the file). Only the autoscaler's host has events, so
the panel takes them from whichever connected gateway has any, independent of which one leads the section. No
events in the window, or a gateway older than the field: no timeline at all. It redraws with the panel's normal
refresh; there is no extra request.

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

Each poll sends one request per connection, not one per profile: the gateway section is machine-wide and only
the CI owner profile serves CI (the others answer `ci.state: unavailable`). The lead route is the CI owner, else
`default`, else the first. Every profile of a connection is fetched (a discovery sweep) until the owner is known,
every 5 min after that, and on the poll after the lead fails; while the lead backs off, one healthy profile
keeps the gateway section fresh. The transport lives per plugin ctx, not per mount: the status bar remounts
its contributions whenever the contribution list changes, and a per-mount transport re-swept on every remount.

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
- one request per connection, owner lead, sweep after a lead failure and every 5 min: "one request per
  connection", "the CI owner route leads", "when the lead fails", "discovery sweeps"
- a remount keeps the transport (no sweep per mount): "a remounted chip keeps the transport"

- CI groups: categories + counts + health, collapsed by default, expand/remember/collapse: `groups.test.mjs`,
  `test_fleet.py` `test_ci_machine_categories_for_the_panel_groups`
- worker lifecycle timeline: `fleet.test.mjs` "timeline: …" (four kinds/colours and accessible labels; position
  proportional to time and out-of-window events dropped; hover text; lanes, spacing and determinism; nothing drawn
  without events or the field, legend with them; events taken from the gateway that has them); ledger read
  (window, order, known kinds, limit, missing file/table, malformed rows, locked database, never created or
  modified, failed `mode=ro` open): `test_fleet.py` `test_worker_events_*`, `test_gateway_section_carries_events_and_the_window_start`,
  `test_scaler_ledger_path_env_override_else_home_state`; REST path: `test_api.py`
  `test_fleet_route_gateway_section_carries_worker_events_from_the_scaler_ledger`. **Not tested**: the autoscaler
  writing the ledger (not part of this plugin), a ledger in WAL mode under a live writer, and the tick colours as
  drawn in the Desktop's light and dark themes (CSS).
- gateway rows are single grid lines under one column header (bare numbers, units in the header; flags and load age as hover text on an (i) marker only), no Investigate control: "gateway rows: one grid line", "waiting: header counts", "load meters"

Not covered: native Desktop rendering (checked by hand after hermes-sync).
