# Audited, bounded diagnostic quotation

Mirror entries: 5224, 5225, 5226, 5227, 5228, 5229, 5230. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

`hermes kanban --board <board> correct-diagnostics <task-id> --request-file <path>` quotes one exact diagnostic span in the task body and one existing comment. It does not waive delivery requirements or change acceptance criteria, lifecycle, comment identity/author/timestamps, dependencies, or notification bookkeeping.

The task must be inactive and open, the comment UID must identify that task, and a synced board must be the owning primary. Worker-marked and delegated-child calls are refused. An identical request can return its original audit only while the corrected body and comment still match; conflicting request IDs and drift refuse.

## How it works

The request JSON has exactly seven keys: `comment_uid`, `expected_body`, `expected_comment`, `body_diagnostic`, `comment_diagnostic`, `reason`, `request_id`. The file read is capped at 200000 bytes. All fields, including task ID, are nonblank strings with individual UTF-8 byte limits: task ID 64, comment UID 201, body 100000, comment 50000, each diagnostic/reason 2000, request ID 128.

Quotation requires one unambiguous single-line span and inserts quotation marks without rewriting surrounding prose. The Acceptance label is found case-insensitively on the original string, not a case-folded/lowercased copy whose Unicode expansion shifts offsets. The transaction compares exact before values and records before/after, reason, request ID and observed process context in `diagnostic_corrected`. Later tests verify replay against persistent rows, not a mocked event list.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_diagnostic_correction.py`
- `hermes_cli/kanban_parser.py`
- `website/docs/developer-guide/diagnostic-prose-correction.md`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source coverage exercises actual argparse/dispatch, bounded valid JSON as well as bad bytes/deep nesting, field limits/blank fields, worker/delegation fences, malformed audits, request conflicts, drift, task/comment membership, owning-primary restrictions, inactive-open branches and current-run refusal. Sync fixtures check body, comment and audit convergence. Unicode regressions include expanding case mappings before Acceptance (including U+0130) so an incorrect offset calculation is observable.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_diagnostic_correction.py`
- `tests/hermes_cli/test_kanban_diagnostic_correction_cli.py`
- `tests/hermes_cli/test_kanban_diagnostic_correction_controls.py`
- `tests/hermes_cli/test_kanban_diagnostic_correction_sync.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

The authority model is trusted operator, not authentication: absence of worker markers and a session hint do not prove independent human or author custody. Keep that authorization evidence separately. There is no force/actor flag. The maintenance handler skips explicit `init_db`, but opening the connection still performs normal schema setup and may create a missing DB; the sync schema is required. It is not a read-only missing-board probe.

## Why core instead of a plugin

The correction must preserve Kanban's transaction, sync, lifecycle and delegation fences together with durable audit semantics. A plugin that edits prose after the fact cannot safely replace these database/CLI invariants.
