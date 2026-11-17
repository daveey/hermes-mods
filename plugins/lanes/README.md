# lanes

REST API behind the Desktop lanes panel (`desktop-plugins/lanes`). All data and writes go through core's
`hermes kanban lanes` (`hermes_cli/kanban_lanes*.py`); this plugin adds no state of its own.

| Route | What |
|---|---|
| `GET /api/plugins/lanes/v1/lanes?hours=1\|6\|24\|168` | Per lane: target and actual share, running, ready, held, open, done, tokens, estimated cost; the planner's latest notes |
| `PUT /api/plugins/lanes/v1/lanes/<lane>` | `{"share": n, "paused": bool, "admit_per_day": n}`, any subset |
| `GET /api/plugins/lanes/v1/held?lane=x` | Cards waiting at a lane gate |
| `POST /api/plugins/lanes/v1/held/<card>/admit` | Release one, `{"reason": "..."}` |

On a core without lanes support, or with no `kanban.admission.lanes`, reads answer
`{"configured": false, "reason": ...}` and writes answer 409. Cost is an estimate from the runs whose workers
reported usage; `usage_runs` against `runs` says how many that is.

Enable: `hermes plugins enable lanes`. Tests: `scripts/test-all -k plugins/lanes`,
`node --test desktop-plugins/lanes/tests/lanes.test.mjs`.
