# PR-guard comment provenance and soft duplicate check handling

Patch coverage: 6799, 6800, 6836, 6837, 6838.

## Behavior

- A valid spend-review system event that has a portable comment UID but no host-local comment id is now accepted by the PR guards when exactly one portable-UID row matches; malformed ids, unknown/duplicate UIDs, author/length checks and own-worker publications are preserved. Previously the all-event decoder rejected it and dropped all later provenance, misclassifying a dispatcher CI wake. Legacy watermark lookup ignores UID-only events safely; malformed UID payloads are rejected.
- Required merge-time check selection: when the latest row for a required name/app is skipped or neutral, the latest substantive completed pre-merge success from the same app is retained. A success superseded by a substantive failure/pending run is never resurrected; required-context aggregation satisfies soft duplicates only alongside a completed exact-head success from their same app. Legacy statuses, no-required fallback, other classifications, merge cutoffs and app isolation are unchanged. A completed success without a completion time stays non-success.

## How it works

`hermes_cli/kanban_db_dispatch_pr_state.py` (UID-only event resolution), `kanban_pr_acceptance.py` and `kanban_pr_merge_time.py` (soft-duplicate reduction).

## Code

`hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_pr_acceptance.py`, `hermes_cli/kanban_pr_merge_time.py`.

## Tests

Read from source; **not executed here**. `test_kanban_pr_uid_only_event.py`, `test_kanban_pr_acceptance_duplicates.py` (including guard-removal mutants named in commit text, owner-reported). Behavior against a real code host API is **untested**; the commit text mentions a frozen-API replay as an owner observation only.

## Applying

Apply in ordinal order.

## Gotchas

- Soft rows count only with a same-app exact-head completed success.

## Why core, not a plugin?

Kanban PR acceptance logic.
