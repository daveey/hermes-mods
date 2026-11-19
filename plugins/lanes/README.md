# lanes

REST API behind the Desktop lanes panel (`desktop-plugins/lanes`). All data and writes go through core's
`hermes kanban lanes` (`hermes_cli/kanban_lanes*.py`); this plugin adds no state of its own.

| Route | What |
|---|---|
| `GET /api/plugins/lanes/v1/lanes?hours=1\|6\|24\|168` | Per lane: target and actual share, running, ready, held, blocked_on_you (human asks), open, done, tokens, estimated cost; the planner's latest notes |
| `PUT /api/plugins/lanes/v1/lanes/<lane>` | `{"share": n, "paused": bool, "admit_per_day": n}`, any subset |
| `GET /api/plugins/lanes/v1/held?lane=x` | Cards waiting at a lane gate |
| `POST /api/plugins/lanes/v1/held/<card>/admit` | Release one, `{"reason": "..."}` |

On a core without lanes support, or with no `kanban.admission.lanes`, `GET /v1/lanes` answers
`{"configured": false, "reason": ...}` and every other route answers 409. The panel uses the lanes read and the
share and pause writes; `admit_per_day` and the held routes are API-only for now. Cost is an estimate from the
runs whose workers reported usage; `usage_runs` against `runs` says how many that is.

The Desktop plugin (`desktop-plugins/lanes`) docks a full-width strip at the top of the window (`shell.topStrip`),
not a status-bar popup. Collapsed (the default, persisted in localStorage) is one line of per-lane pills: colour
dot, name, a small bar (blue running, yellow not ready, red blocked on you) and the open count, plus a toggle.
Clicking a lane expands the strip and scrolls to that row. Expanded is today's panel (sliders, Pause, window,
Board, Apply/Reset, notes) capped at 60vh with internal scroll. Where lanes are configured, the status-bar task
pill and the Show lanes palette command expand it; its Board button opens the board. On an older Desktop SDK
without `TOP_STRIP_AREA`, the plugin still activates using the `shell.topStrip` area string.

Lanes are configured on one gateway (the one that holds the board). The Desktop panel asks every
connection once, by id (answered by that connection's default profile), and reads and writes on the first that
answers `configured: true`, whichever gateway the window has in front,
so the plugin must be enabled on that gateway.

Enable: add `lanes` to the private repo's `home/plugins-enabled.txt` (a core-only host enables it as the
backend of the Desktop plugin it serves). Tests: `scripts/test-all -k plugins/lanes`,
`node --test desktop-plugins/lanes/tests/lanes.test.mjs`.
