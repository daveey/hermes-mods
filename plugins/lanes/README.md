# lanes

REST API behind the Desktop lanes panel (`desktop-plugins/lanes`). All data and writes go through core's
`hermes kanban lanes` (`hermes_cli/kanban_lanes*.py`); this plugin adds no state of its own.

| Route | What |
|---|---|
| `GET /api/plugins/lanes/v1/lanes?hours=1\|6\|24\|168` | Per lane: target and actual share, running, ready, held, blocked_on_you (human asks), open, done, tokens, estimated cost; the planner's latest notes |
| `PUT /api/plugins/lanes/v1/lanes/<lane>` | `{"share": n, "paused": bool, "admit_per_day": n, "cards_per_day": n, "usd_per_day": n}`, any subset (the last two need a core with daily caps) |
| `GET /api/plugins/lanes/v1/lanes/flow?window=1h\|6h\|24h\|7d` | Core's `hermes kanban lanes flow --json`: every open card `{id,title,lane,step,reason,source,age_h,pr}` plus per lane done, run-hours, cost, `limits` and today's `usage` |
| `GET /api/plugins/lanes/v1/held?lane=x` | Cards waiting at a lane gate |
| `POST /api/plugins/lanes/v1/held/<card>/admit` | Release one, `{"reason": "..."}` |

On a core without lanes support, or with no `kanban.admission.lanes`, `GET /v1/lanes` answers
`{"configured": false, "reason": ...}` and every other route answers 409. The panel uses the lanes read and the
share and pause writes; `admit_per_day` and the held routes are API-only for now. Cost is an estimate from the
runs whose workers reported usage; `usage_runs` against `runs` says how many that is.

The Desktop plugin (`desktop-plugins/lanes`) docks a full-width strip at the top of the window (`shell.topStrip`),
not a status-bar popup. Collapsed (the default, persisted in localStorage) is one line of per-lane pills: colour
dot, name, a small bar (blue running, yellow not ready, red blocked on you) and the open count, plus a toggle.
Clicking a lane expands the strip and scrolls to that row. Expanded is two tabs (the last one is remembered in
localStorage), capped at 60vh with internal scroll. **Floor**: lanes are rows, the ten steps (intake, waiting, ready,
running, review, ci, merge, deploy, test, done; plus an Unclassified column only while a card sits in it) are columns,
one box per open card coloured by why it waits (hover = title, click = open the card); a cell with 4+ cards or any
card that needs you is hatched. Each lane row carries its valves: share slider and Pause, a running-workers gauge,
and cards/day and $/day inputs with today's usage; Apply writes the changes through `PUT` (`lanes set`), Reset drops
them. **Flow**: an SVG Sankey of sources (by project, or by session/automation) into lanes and then into flowing
steps or dams named by reason, each dam saying what clears it (the PR whose merge frees the most cards, asks to
answer, a cap to raise), with a limits line below. Both read `/v1/lanes/flow` only while expanded. The workers valve
is a gauge: core's `lanes set` has no max-workers write yet. Window, Board, Apply/Reset and notes as before. Where lanes are configured, the status-bar task
pill and the Show lanes palette command expand it; its Board button opens the board. On an older Desktop SDK
without `TOP_STRIP_AREA`, the plugin still activates using the `shell.topStrip` area string.

Lanes are configured on one gateway (the one that holds the board). The Desktop panel asks every
connection once, by id (answered by that connection's default profile), and reads and writes on the first that
answers `configured: true`, whichever gateway the window has in front,
so the plugin must be enabled on that gateway.

Enable: add `lanes` to the private repo's `home/plugins-enabled.txt` (a core-only host enables it as the
backend of the Desktop plugin it serves). Tests: `scripts/test-all -k plugins/lanes`,
`node --test desktop-plugins/lanes/tests/lanes.test.mjs`.
