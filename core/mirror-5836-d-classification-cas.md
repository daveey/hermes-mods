# Non-releasing canonical classification CAS

Source patch ordinals: 5814–5815.

## Behavior

An authenticated PRIMARY owner operator can request a narrow canonical `reclassify_parked` correction for a withdrawn legacy ask. The card remains blocked; this is not unblock, promotion, a human answer, scope renewal or a budget reset.

## How it works

`block --metadata-only --human-request null --kind dependency --classification-cas <JSON>` routes through the dedicated adapter. Workers, mixed lifecycle arguments, active execution and pending local task edits are refused. The exact operation binds `client_op_id`, task/reason, task revision, `expected_event_uid` plus `expected_event_version`, spend episode, parent/link snapshots, and `disposition_comment_uid` plus `disposition_comment_version`.

The adapter uses authenticated canonical ingress, pulls the certified owner feed and reads canonical row evidence back before confirming convergence. The operation contract changes classification/sync metadata and appends `block_classification_corrected`, not task execution state. Editing either evidence row invalidates its frozen request even if the task revision is unchanged.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_human_request_migration.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_classification_cas.py` covers parser routing, refusal without metadata-only, worker exclusion, exact operation submission, pre-network guards, mixed-argument refusal, canonical rejection without local fallback and successor-ask convergence remaining unconfirmed. Evidence-version assertions cover the revised operation shape.

## Applying

Deploy the matching canonical Board operation before using this owner-side client entry. Retain the exact operation identity and frozen evidence for recovery; do not fabricate a fresh request after uncertain delivery.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Free-form actor strings and null ask metadata are not credentials. An HTTP acknowledgement alone does not prove owner/renderer convergence. This source slice documents the client contract; it is not execution evidence for a deployed Board service.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
