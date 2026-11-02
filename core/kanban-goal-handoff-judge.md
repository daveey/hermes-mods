# Goal-mode judge counts the handoff call as the card's finish step

Patch:

- `patches/0135-fix-kanban-goal-handoff-judge-counts-the-handoff-cal.patch`: backend
  (`hermes_cli/goals.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`).

What you see: a goal-mode Kanban card whose body says "Finish with kanban_request_review" now reaches review when
its work is done. Before, every review handoff was rejected with "the required final kanban_request_review has not
been confirmed", and the worker looped until its goal budget ran out.

## Behavior

- Both terminal-handoff gates, the `kanban_complete` / `kanban_request_review` tools and the `hermes kanban
  complete` / `request-review` CLI, append a grading note to the goal text the judge sees. The note says the call
  being graded is the one being made now, and that any instruction to finish with, hand off via, or request review
  through it (or a Kanban review/complete handoff) is satisfied by this call. The judge grades only the rest.
- The card's real acceptance criteria are kept verbatim. An incomplete summary is still rejected.
- An empty goal still skips the gate, as before.

## How it works

- The gate runs inside the handoff call, before it returns, so the judge could never see that step confirmed.
- `hermes_cli.goals.handoff_judge_goal(title, body, handoff)` builds the goal text. `tools/kanban_tools._goal_gate`
  passes the tool name. `hermes_cli/kanban._goal_mode_handoff_rejection` takes a `handoff_tool` argument, which
  `_goal_gate_error` derives from the handoff verb.
- Live replay of card t_a6e9276d: old 3/3 rejected, new 3/3 done; an incomplete summary is still rejected 3/3.

## Tests

- `tests/hermes_cli/test_kanban_goal_judge_affinity.py`: the tool review gate and the CLI review gate each pass a
  judge that withholds `done` without the note (one test per entry path); the goal text keeps the real criteria,
  names the tool, and an empty goal stays empty.

## Applying

Core-only; no config. Takes effect for workers started on the new core.

## Gotchas

- The note relies on the judge model following it; the live replay above is the evidence, not a guarantee.

## Plugin?

No: the goal text is built inside the core handoff gates.

## Follow-up patches (1540–1541): judged-unachievable goals use bounded recovery

Patches: `patches/1540-fix-kanban-route-judged-unachievable-goals-through-b.patch` (`ac7f935cd60`) and
`patches/1541-fix-kanban-route-judged-unachievable-goals-through-b.patch` (`c538e9b9723`), both "fix(kanban): route
judged unachievable goals through bounded recovery". 1541 is a byte-identical duplicate source commit; its diff was
already applied by 1540, so 1541 is kept in the series as an explicit **empty** entry (message only, no diff).

**Behavior.** When the goal judge returns a parsed `blocked` (unachievable) verdict, the card is no longer left in a
generic block that asks for a human. It is parked in `triage` with block kind `transient` and
`rekind_reason=goal_unachievable`, its body and acceptance criteria unchanged. A recovery card is then filed for it
("Recover <id>: goal unachievable") that tells the recovery worker to fix the missing prerequisite and, only once it
is proven repaired, resume the original via `kanban_supervisor_request` (`op: resume`). The existing two-resume
lifetime bound applies. Parked goals are kept out of the triage specifier, specify-all selection and the stale-triage
sweep, so they can't cycle back through generic triage. An unparsed `blocked` answer is treated as `continue` and
never parks the card.

**How it works.** New `hermes_cli/kanban_goal_recovery.py`. `park()` calls `block_task(..., goal_rejection=GoalRejection(digest))`.
Inside the block transaction, `record()` writes a kernel `goal_unachievable` event only when the card is goal-mode,
`running`, owned by the expected run, has no human request or human block kind, and the digest matches the current
task and reason. Worker-supplied block metadata can't mint this receipt. `classify_stop` routes a transient block with
a valid receipt to `goal_unachievable`. `exhaustion_refusal` allows a resume only when that receipt belongs to the
parked run. `kanban_resume_requester` files the goal recovery card with the target's model/provider pins and
`requires=[terminal, file, kanban]`. Its assignee comes from `recovery_assignee`: the target's own assignee, then
`kanban.orchestrator_profile`, then `default`. Queue assignees and profiles that are missing or lack those
capabilities are skipped. If none qualifies, no card is filed and the goal stays parked. Four entry points call `park`:
the `kanban_complete`/`kanban_request_review` tool gate (`tools/kanban_tools_completion.park_unachievable`), the CLI
`_goal_gate_error`, the CLI goal loop (`cli_single_query`) and the gateway goal loop (`session_kanban`). The last two
pass it as `run_kanban_goal_loop(unachievable_fn=...)`. A refused or failed park stops the loop as failed. Callers that
pass no `unachievable_fn` keep the old block.

**Code.** `hermes_cli/kanban_goal_recovery.py` (new), `goals.py`, `kanban.py`, `kanban_block_routing.py`
(`validate_block_request`/`clear_meta` moved here from `kanban_db.py`), `kanban_db.py`, `kanban_db_schedule.py`,
`kanban_resume_exec.py`, `kanban_resume_requester.py`, `kanban_specify.py`, `kanban_triage_sweep.py`,
`gateway/session_kanban.py`, `hermes_cli/cli_single_query.py`, `tools/kanban_tools.py`, `tools/kanban_tools_completion.py`.

**Tests** (in the commit; not run for this mirror):
- `tests/hermes_cli/test_kanban_goal_recovery.py`: tool gate for both handoffs
  (`test_judge_park_creates_fresh_bounded_requester_without_changing_goal`), CLI gate for both handoffs
  (`test_cli_gate_parks`), CLI and gateway loops (`test_goal_loop_blocked_callback_parks`), pins kept and no new
  recovery card after two resumes (`test_goal_recovery_pins_and_no_triage_cycle_after_two_resumes`).
- `tests/hermes_cli/test_kanban_goal_recovery_guards.py`: receipt refused for missing/stale run, non-goal, human,
  forged or changed goal; resume denied on a missing, forged or stale receipt; unparsed `blocked` mints no receipt
  (CLI and tool); assignee fallback (`default`/orchestrator/none); a handoff matrix (cli/tool × complete/review ×
  blocked/parse/transport/absent); loop parse/transport/changed-goal failures mint no receipt; the resume rechecks the
  target after the request; no eligible profile keeps the goal parked; the dependency gate is preserved.
- `tests/hermes_cli/test_kanban_goal_recovery_selection.py`: individual specify refuses a parked goal before the aux
  call; specify-all and auto-decompose selection exclude it.

**Applying.** Depends on the preceding series. Apply the full ordered series (1541 applies as an empty commit) per the
README "Applying" section. Standalone application is not certified.

**Gotchas.** The tool-gate rejection messages (`GOAL_GATE_MESSAGES`) moved to `tools/kanban_tools_completion.py`.
They still appear when a park is refused, for example when there is no worker run id. Without a configured, capable
recovery profile the card stays parked in triage with no recovery card: this is deliberate and fails closed.
