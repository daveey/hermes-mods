# Lost-review result and worker-session entry-path coverage

Patches **2164–2166** are regression-test and comment changes, not new production behavior.

## Behavior

The tests pin existing rules: a lost review that names its report only in completion `result`, rather than `summary`, remains held; dispatcher worker tools stamp their own `worker_session_id`; callers operating on another card do not stamp that caller's session onto the ended run.

## How it works

2164 parametrizes the report-file case over `summary` and `result`, and clarifies `_LOST_REVIEW_KEYS` documentation: `kanban_complete`, `kanban_continue` and `kanban_request_review` can stamp worker identity, while completion prose can add artifacts. The production change is comment-only. 2165 invokes the continue and request-review handlers with worker task/run/session context and reads the ended run metadata. 2166 gives those cases distinct session values and adds explicit-task, non-worker cases for all three tools, asserting both the outcome and absence of a session stamp.

## Code

`hermes_cli/kanban_dependency_review.py` (comment only), `tests/hermes_cli/test_kanban_dependency_review_no_verdict.py`.

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.**

- `test_lost_review_citing_its_report_file_stays_held[summary|result]`.
- `test_continue_and_request_review_stamp_worker_session_id[continue|request_review]` exercises each tool handler and persisted metadata.
- `test_worker_tools_stamp_no_session_id_for_another_caller[complete|continue|request_review]` exercises each non-worker handler path.

These are core/tool-handler regressions, not CLI, REST or dashboard UI tests. The patches do not add a new complete-tool positive case; they retain the prior coverage for it.

## Applying

Follow the complete ordered replay in [README.md](README.md), including replay plan and bridges. These test-only follow-ups depend on earlier review/completion implementation and are not an independent patch stack.

## Gotchas

A summary-only test cannot establish result-only artifact discovery. A correct worker stamp also needs the negative non-worker cases: having a session ID alone must not imply worker ownership.

## Plugin?

No runtime feature is introduced. These tests protect the core review classifier and tool-handler metadata contract that a dashboard may consume.
