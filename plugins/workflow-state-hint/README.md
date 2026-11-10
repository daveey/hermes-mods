# workflow-state-hint

A project-work workflow (`plugins/project-work`) runs as implement/review/release Kanban cards, but its own state
lives in `<HERMES_HOME>/project-work/ledger.sqlite3` (and `<HERMES_HOME>/profiles/*/project-work/ledger.sqlite3`),
which no Kanban tool shows. A card moving from blocked to review is therefore easy to report as "recovered" while
the workflow still has an error and has published nothing.

This `transform_tool_result` hook scans `kanban_show`/`kanban_list`/`terminal`/`execute_code`/`tool_call` results
for card ids (`t_<16 hex>`). For each matching workflow whose `error` is set or whose `state` is not finished
(`posted`, `archived`, `invalidated`, ...), it adds one line (key `workflow_state` on JSON results, appended text
otherwise):

```
[workflow-state] card t_X (implement stage) belongs to project-work workflow <id>: state=running,
error=WorkflowError: ..., published=no; other cards: review t_Y, release t_Z. A card status change is not
workflow recovery; say which stage is still open.
```

At most 3 workflows per result, 100 ms budget, read-only (`mode=rw` + `query_only`, never creates a file), cached
by ledger mtime/size. A missing, locked or corrupt ledger leaves the result unchanged.

Tests: `scripts/test-all -k workflow-state-hint`. Undo: `hermes plugins disable workflow-state-hint`.
