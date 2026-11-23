# Owner-local inactive text compare-and-swap

Patch: **5917**.

## Behavior

A trusted owner-local operator can correct an inactive card's title/body with exact compare-and-swap and an atomic audit receipt:

```sh
hermes kanban --board default edit-text TASK_ID --request-file request.json
```

The Python entry is `hermes_cli.kanban_text_edit.edit_text(conn, task_id, request)`. No tool, REST, dashboard or distributed courier entry is added. Workers and delegated children refuse. This is text maintenance, not release, admission, author authentication or a lifecycle edit.

## How it works

The request schema is exact:

```json
{
  "request_id": "operator-request-1",
  "reason": "Correct superseded routing prose",
  "expected": {"title": "Existing title", "body": "Exact old text"},
  "replacement": {"title": "Existing title", "body": "Exact new text"}
}
```

Both pairs are mandatory; null body differs from empty body. No trimming or Unicode normalization occurs. Unknown/duplicate keys, invalid UTF-8, nonfinite JSON, no-op requests and invalid model-neutral replacement text refuse. UTF-8 byte limits are 500000 for the file, 4000 per title, 100000 per body and 2000 for reason. Titles/reason are nonblank. Request ID is 1–128 ASCII letters/digits/underscore/hyphen.

The existing edit transaction rechecks strict owner identity, inactive custody, prior receipts and both exact preimages. Allowed statuses are todo/ready/blocked/scheduled/triage, with no current/open run, claim fields, worker PID or live/unknown-expiry lease (even own-gateway leases). It changes only title/body and emits `text_cas_edited`, not the ordinary `edited` progress event. Holds, diagnostics, dependencies, decomposition, admission, leases and original event history remain intact. Only the new receipt gets a stable UID; legacy nullable UID schema addition, when needed, shares the transaction.

Identical local receipt plus current replacement returns `replayed` without writing/notifying. Changed request contents, current-state drift, foreign-origin receipts and malformed receipts fail closed. Audit failure rolls back. Storage or postcommit response errors may return `unknown` even after commit.

## Code

- `hermes_cli/kanban_text_edit.py`: strict parser, inactive/owner checks, `TextCAS`, replay and CLI.
- `hermes_cli/kanban_db_edit.py`: exact internal hook in the existing transaction; mixed edit options forbidden.
- `hermes_cli/kanban.py`, `kanban_parser.py`: CLI and delegated-child fence.
- `website/docs/developer-guide/owner-text-cas.md`: source interface contract.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_text_edit.py`: apply/replay, null/empty preservation, stale title/body, inactive holds, custody refusals, request conflict/drift, audit rollback, worker/input denial and stamp-stable replay.
- `tests/hermes_cli/test_kanban_text_edit_cli.py`: registered CLI apply/replay without initialization, malformed files, worker refusal and stale snapshot preservation.
- `tests/hermes_cli/test_kanban_text_edit_contract.py`: concurrent CAS winner, postcommit-unknown recovery with the same identity, strict owner/lease/open-run checks, atomic sync outbox, legacy history preservation, corrupted receipts, no-progress and diagnostic/decomposition invariants, mixed-option rejection and replacement policy.

Other entry paths are not added or tested by this feature.

## Applying

Use the full snapshot replay in `core/README.md`. Port the transaction hook, receipt rules and CLI fences together; do not emulate it with ordinary edit plus comment. CLI exits 0 for applied/replayed, 2 for invalid input or worker refusal, otherwise 1. Applying the patch does not authorize production-card edits.

## Gotchas

Marker absence is not authentication: this assumes a trusted operator process. It does not clean stale custody or edit running/review/terminal cards. On unknown outcome, retain the exact request and inspect target/history before deciding; never mint another identity or automatically resubmit. A local committed receipt is not sync convergence. Audit process context is explicitly non-author evidence.

## plugin?

Core transaction/CLI maintenance interface only. A plugin must not expose it as worker authority or a remote unauthenticated edit API.
