# Goal continuations: a status-only turn is not progress

Patch **0522** (source `5dbfc07e82`).

## Behavior

Every `/goal` continuation prompt (plain, with a completion contract, with `/subgoal` criteria) and the Kanban
goal-mode worker continuation now also say: "A turn that only restates status is not progress. Take the next
concrete action, or say exactly what blocks it."

Why: a judged sample of automated turns (Oct 16-27) found ~56% of goal continuations ending as status reports on a
fixable problem; one session ended 7 consecutive goal turns with "The overall goal is still incomplete; no user input
is needed."

## How it works

The sentence is spliced into the four template constants in `hermes_cli/goals.py`, right after "Take the next
concrete step", so every continuation path that formats them carries it. Judge prompts are unchanged.

## Code

- `hermes_cli/goals.py`: `CONTINUATION_PROMPT_TEMPLATE`, `CONTINUATION_PROMPT_WITH_CONTRACT_TEMPLATE`,
  `CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE`, `KANBAN_GOAL_CONTINUATION_TEMPLATE` (`[daveey]` comments).

## Tests

- `tests/hermes_cli/test_goals.py::TestGoalManager::test_continuation_prompts_reject_status_only_turns`: the rule is in
  all four templates and in `GoalManager.next_continuation_prompt()` output (entry path: interactive `/goal` loop).
  The Kanban worker path is covered by the template assertion only; its loop wiring is untested here.

## Applying

`git am -3 core/patches/0522-*.patch` on top of the series. Prompt-text only; no config.

## Gotchas

- Upstream rewording of these templates conflicts; keep the sentence after "Take the next concrete step".

## Plugin?

No: the templates are module constants read inside core's goal loop; no hook exposes them.
