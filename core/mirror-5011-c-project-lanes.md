# Project lanes: classify, gate, share, CLI, and shadow mode

Patches: **4620–4631, 4636** (5011 snapshot).

## Behavior

When `kanban.admission.lanes` is configured, each card gets a lane (`tasks.tenant`). Agent-filed cards in a gated lane wait in triage with a priority cap; a person's own cards and alarms do not. Soft shares reorder dispatch without hard caps except pause. Unconfigured lanes are a no-op. Malformed mappings log and read as no lanes.

`hermes kanban lanes` shows, assigns, admits, pins, sets share/budget/pause, and applies a planner action list with fixed limits. Reports include completions, tokens, and estimated cost. Shadow mode (`lane_shadow`) classifies and reports without holding, capping, or reordering.

Admit only moves a triage-parked card. A person's typed CLI create/admit stays outside the planner daily budget; an agent's admit is limited. Decompose/specify/`--force` decompose refuse gate-held cards. Shadow mode stores `created_by` as given.

## How it works

Classification uses match rules over a tenant an agent passed. Human-shell detection, not `--created-by`, decides person vs agent. Config outages keep the last good policy; malformed config turns lanes off. Held means triage + gate mark + never started + never released. Link release needs a runnable waiter and respects `never_admit`. Kernel recovery cards are not held. Plan file I/O uses explicit encodings. `lanes set` is a callable for the panel API.

## Code

- `hermes_cli/kanban_lanes.py`
- `hermes_cli/kanban_lanes_cli.py`
- `hermes_cli/kanban_lanes_plan.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_dependency_validation.py`
- `hermes_cli/kanban_triage_sweep.py`
- `hermes_cli/kanban_decompose.py`
- `hermes_cli/kanban_specify.py`
- `hermes_cli/kanban_selfwork_intake.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `website/docs/user-guide/configuration.md`
- `website/docs/user-guide/features/kanban.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_lanes.py`
- `tests/hermes_cli/test_kanban_lanes_plan.py`
- `tests/tools/test_kanban_lanes_tool_create.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Review/land work of an admitted card must be able to finish it. Relane never leaves a gated lane. Worker-shell create is an agent whatever `created_by` says. Unreadable config must not stop creation. Cost is only for runs that reported usage. Delegated children cannot run lanes.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
