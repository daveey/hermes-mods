# Read-only Kanban actionability and stranded-card reports

Patches: **5897–5905**.

## Behavior

`hermes kanban stranded [--json] [--limit N] [--after TASK_ID]` reports the administrative next action, accountable owner, blocker, recorded escalation deadline, evidence uncertainty and contract violations for nonterminal cards. Counts cover the entire selected-board read snapshot; pagination limits only displayed rows. Limit is 1–1000, default 100.

CLI show, the show tool and worker context carry a bounded per-card actionability projection, not a whole-board report. Terminal cards explicitly have `no_open_action`, including when optional evidence fails. Replica evidence cannot authorize local execution.

## How it works

A pure reducer consumes SQLite-derived facts. Pending stop custody, protected stops, containment, validated human steps and dependencies take precedence over ordinary status. Running rows do not prove executor liveness; ready/review rows do not prove admission eligibility. Contract identity derives from material episode/source/dependency/request coordinates, not notices, delivery-state changes or the report clock.

The CLI opens SQLite read-only with `query_only`, skips initialization/sync/network and streams task IDs in batches of 128 inside one read snapshot. One Reader caches observed schema and is reused across rows. It leaves an existing caller transaction intact; it rolls back only a snapshot it opened.

Optional missing schemas, malformed payloads, unresolved receipts, replica freshness and unavailable policy evidence remain UNKNOWN rather than becoming absence. Administrative ownership is scoped to the stop episode: a closed/held carrier is unavailable, and a carrier with pending canonical receipt custody is unknown. Notice timestamps cannot renew a deadline. Schedules require valid named provenance or an appropriate recorded probe; a probe cannot conceal malformed provenance. Existing internal `resource` producers remain readable when they have a nonblank reference. A CI `why` alone is not its required reference.

## Code

- `hermes_cli/kanban_actionability.py`: pure projection and provenance validation.
- `hermes_cli/kanban_actionability_sources.py`: defensive Reader and episode/parent/carrier adapters.
- `hermes_cli/kanban_actionability_cli.py`: streamed report and CLI output.
- `hermes_cli/kanban_actionability_evidence.py`: fault-contained fields and 1800-character rendering.
- `hermes_cli/kanban.py`, `kanban_parser.py`, `kanban_shadow_view.py`, `kanban_db_context.py`; `tools/kanban_show.py`: entry-point carriage. Context is appended after structural workspace fields to preserve offsets.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_actionability_report.py`: registered CLI bypasses initialization; human/JSON pagination, complete counts, multi-status coverage, snapshot consistency, subprocess database-byte preservation, real stop/dependency/reclaim policy, show-tool and worker-context no-write/no-report boundaries, workspace offsets and terminal fallback.
- `tests/hermes_cli/test_kanban_actionability_contract.py`: reducer precedence and identity; transactions and multi-batch streaming; cached-reader reuse; independently damaged schema/evidence seams; exact episode ownership and deadlines; malformed schedules, pending carrier receipts, internal resource compatibility and CI-reference controls. Follow-ups 5899–5902 strengthen adapter isolation; 5903–5905 cover provenance/custody composition and compatibility.

These tests do not constitute a live fleet audit or prove that all cards have recoverable owners.

## Applying

Use the complete snapshot replay recipe in `core/README.md`; these ordinals depend on earlier lifecycle, scope-renewal and read-only reader features. For a port, retain the reducer/adapters split and wire every listed entry separately. Do not turn the report into a repair writer.

## Gotchas

Zero known violations is not invariant success while `unknown_count` is nonzero. Displayed release conditions are evidence, never authority; comments, timers and notices cannot release held scope. A healthy administrative carrier does not satisfy the root dependency. Per-card context is bounded in rendering and immediate-parent scope, not a claim that arbitrary history is constant-cost.

## plugin?

The report is core because it reuses internal lifecycle readers and CLI/context plumbing. A plugin may present its result, but must not infer admission or release authority from it.
