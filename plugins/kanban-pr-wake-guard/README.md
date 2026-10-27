# Kanban PR wake guard

Pre-tool-call guard for `kanban_block` and `kanban_schedule`, including nested deferred `tool_call` batches.
Raw `gh pr view`, `gh pr status`, or `gh api .../pulls/N` probes mentioning state or merge status are refused.
Graphite queues can land changes while closing the original PR rather than marking it MERGED.

Use `hermes --run-module hermes_cli.kanban_pr_wake https://github.com/OWNER/REPO/pull/N`.
For replacement commits while the original stays open, provide both `--superseding-commit` and
`--verification-job`. Waking means reconcile, not accept delivery.

Other PR probes (e.g. reviewDecision) remain allowed; the result transformer appends a warning that closure
can strand their condition. JSON object results keep their fields and receive a warning field.

Install with the repository installer, or link this directory into `$HERMES_HOME/plugins/kanban-pr-wake-guard`
and run `hermes plugins enable kanban-pr-wake-guard`. New runtimes discover it; existing sessions require a
normal safe reload. No service restart is performed by this plugin.

Tests: `python3 -B -m unittest discover -s plugins/kanban-pr-wake-guard/tests` from the repo root.

Scope: heuristic inspection of inline shell text, not a shell security sandbox. Script files, dynamically
constructed commands, direct DB/CLI operations, and unrelated tools are not inspected. A probe invoking
`hermes --run-module hermes_cli.kanban_pr_wake` is exempt; a comment mentioning it is not. The guard does not rewrite existing parked cards automatically.
