# Kanban block guidance matches the ownership guard

## Behavior

Patch **1133** aligns worker prompts, refusal messages, and block/create tool descriptions with the existing structured-human-request contract. Tool and CLI refusals include a fillable `human_request` skeleton; agent dependencies are directed toward parents, scheduling, assignment, or capability ownership instead of a human inbox.

The patch also permits the **database-verified** continuation-budget exhaustion handoff through tool and CLI guards so the existing database routing can send it to agent triage. A fabricated exhaustion prefix or extra approval prose does not get that exception.

## How it works

`block_policy_error` accepts verified exhaustion state (or `(conn, task_id)` to compute it) through `is_exhausted_budget_block`. The tool supplies the task; the CLI verifies all requested IDs before admitting the batch. Other existing exemptions remain: this is not a universal refusal of every request-free block.

Refusal skeletons name `action`, `why_human`, `interaction`, `scope`, and `evidence`, including explicit unblock/archive consequences. `creation_lint_error` directs blocked creates without a request to parents or scheduling. Prompts and stop/recovery nudges now show typed blocks with `human_request`, rather than bare `kanban_block(reason=...)`.

Continuation exhaustion guidance requires the prescribed reason verbatim:

```text
continuation_exhausted: split the work or reset the continuation budget
```

Put handoff context in a comment first. The exception does not make budget resets or task splitting into human decisions.

## Code

- `hermes_cli/kanban_block_policy.py`: `HUMAN_REQUEST_SKELETON`, `block_policy_error`, `creation_lint_error`.
- `hermes_cli/kanban.py::_cmd_block`, `tools/kanban_tools.py::_handle_block`: entry-point exhaustion verification.
- `hermes_cli/kanban_continuation.py::continue_task`: prescribed handoff guidance.
- `agent/prompt_builder.py`, `agent/kanban_stop.py`, `agent/kanban_turn_recovery.py`: worker and nudge guidance.
- `tools/kanban_tools_schemas.py`: create status and block kind/reason descriptions.

## Tests

Tests identified from source, **not executed in this documentation pass**. Patch 1133 adds `tests/tools/test_kanban_agent_owned_block_guidance.py`:

- `test_tool_block_refusal_has_human_request_skeleton`, `test_cli_block_refusal_has_human_request_skeleton`: refused requests teach the structured fix and leave card state unchanged.
- `test_tool_create_blocked_refusal_has_skeleton_and_agent_wait_routes`, `test_cli_create_blocked_refusal_has_skeleton_and_agent_wait_routes`: no rejected card is created; refusal names parents/scheduling.
- `test_create_blocked_with_human_request_still_allowed`: internal database API acceptance, not a successful tool/CLI human-request integration test.
- `test_schemas_say_blocked_and_human_kinds_need_human_request`, `test_worker_prompt_never_prescribes_bare_kanban_block`: schema/string and source inspection assertions, not an LLM-behavior evaluation.
- `test_tool_block_with_prescribed_exhaustion_reason_lands_in_agent_triage`, `test_cli_block_with_prescribed_exhaustion_reason_lands_in_agent_triage`: real fixture exhaustion state through each entry point.
- `test_exhaustion_prefix_without_a_real_refusal_is_still_refused`, `test_exhaustion_with_extra_prose_is_still_refused`: tool-side negative cases.

## Applying

Replay 1133 in the full ordered fork series with preceding patches (`git am -3 --empty=keep` from the documented base). Existing block-policy, human-request validation, continuation-recovery, and triage routing are dependencies. Neither standalone application nor runtime tests were executed here.

## Gotchas

A request in `reason` or create `body` is not a substitute for `human_request`. The exception requires actual exhausted-budget state and the recognized reason, not just `kind='transient'`. Prompt wording checks do not prove fewer refusals in production. Batch CLI behavior is visible in the implementation but has no separate batch test added here.

## Plugin

No plugin is added. Entry-point guard decisions, core prompt generation, and the tool schema contract must agree; a plugin-only message rewrite would not supply the exhaustion verification.
