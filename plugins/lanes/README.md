# lanes

REST API behind the Desktop lanes panel (`desktop-plugins/lanes`). All data and writes go through core's
`hermes kanban lanes` (`hermes_cli/kanban_lanes*.py`); this plugin adds no state of its own.

| Route | What |
|---|---|
| `GET /api/plugins/lanes/v1/lanes?hours=1\|6\|24\|168` | Per lane: target and actual share, running, ready, held, open, done, tokens, estimated cost; the planner's latest notes |
| `PUT /api/plugins/lanes/v1/lanes/<lane>` | `{"share": n, "paused": bool, "admit_per_day": n}`, any subset |
| `GET /api/plugins/lanes/v1/held?lane=x` | Cards waiting at a lane gate |
| `POST /api/plugins/lanes/v1/held/<card>/admit` | Release one, `{"reason": "..."}` |

On a core without lanes support, or with no `kanban.admission.lanes`, `GET /v1/lanes` answers
`{"configured": false, "reason": ...}` and every other route answers 409. The panel uses the lanes read and the
share and pause writes; `admit_per_day` and the held routes are API-only for now. Cost is an estimate from the
runs whose workers reported usage; `usage_runs` against `runs` says how many that is.

Lanes are configured on one gateway (the one that holds the board). The Desktop panel asks the gateway it is
attached to first, then every other connection (answered by that connection's default profile), and reads and
writes on the first that answers `configured: true`,
so the plugin must be enabled on that gateway.

Enable: add `lanes` to the private repo's `home/plugins-enabled.txt` (a core-only host enables it as the
backend of the Desktop plugin it serves). Tests: `scripts/test-all -k plugins/lanes`,
`node --test desktop-plugins/lanes/tests/lanes.test.mjs`.
