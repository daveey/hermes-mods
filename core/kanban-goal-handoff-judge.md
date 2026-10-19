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
