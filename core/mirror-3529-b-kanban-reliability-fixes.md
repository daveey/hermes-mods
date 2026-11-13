# Kanban CI progress, worker eviction and remote notice fixes

Patches: **3513, 3516–3517, 3519**.

## Behavior
- CI progress: when neither runner list is readable (403/404), a self-hosted job queued past the limit is no longer reported as `not_starting` if a job on exactly the same labels is running or recently started. It is reported as `busy` (park allowed, nobody woken). With no such job, `not_starting` is kept. GitHub-hosted labels read no run lists.
- Worker eviction: a worker no longer fails every pickup because an adopted foreign copy has an origin-binding row. Such copies are kept in every unclaimed state; a copy that still cannot be deleted is kept and logged, and other evictions proceed. Pickup logs and continues if eviction raises, so imports always run.
- Remote notices: the open-chat skip re-checks the poller's claim row past the grace window, so a claim released undelivered or held by a dead backend logs the INFO line; healthy claims stay at DEBUG. A card linked to a lineage ancestor no longer produces a spurious INFO line. Logging only.
- 3519 corrects a docstring only; no behavior change.

## How it works
3513 adds a fallback in `kanban_ci_progress` that, with unreadable runner lists, looks at this run's jobs and up to 4 recent in-progress or recently updated completed runs for a job with identical labels that is running or started within the queue limit; reads respect the sweep deadline (over budget defers). 3516 keys the poller claim marker by the link session on both sides in `kanban_remote_notices` and reads the ledger row once past grace (delivered rows are cached; an unreadable ledger counts as held). 3517 excludes copies with an `origin_creation_intents` row from `evict()`, deletes each copy in its own savepoint, and wraps eviction in `pickup()`. 3519 updates the `_collect_kanban_notifications` docstring to say the sweep skips live owned chats.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_worker_node.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/session_notifications.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_unreadable_runners_with_a_job_running_on_the_labels_neither_wakes_nor_blocks`, `test_unreadable_runners_with_a_recently_started_job_on_the_labels_parks`, `test_unreadable_runners_and_no_recent_job_on_the_labels_is_not_starting`, `test_github_hosted_labels_read_no_recent_runs`.
- `tests/tui_gateway/test_kanban_remote_notices.py`: `test_open_chat_skip_logs_info_once_a_poller_claim_is_released_undelivered`, `test_open_chat_skip_finds_the_poller_claim_of_a_card_linked_to_a_lineage_ancestor`.
- `tests/hermes_cli/test_kanban_evict_origin_intent.py`: `test_an_adopted_archived_copy_stays_and_pickup_still_imports`, `test_an_adopted_copy_is_kept_in_every_unclaimed_state`, `test_a_copy_that_cannot_be_deleted_is_kept_and_the_others_go`, `test_pickup_goes_on_when_eviction_raises`.
- `tests/hermes_cli/test_kanban_scratch_custody.py`: modified to expect the refused copy kept; no new test names.
- 3519: untested (docstring only).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The CI fallback returns `busy`, not `ok`; the next sweep re-reads.
- Intent rows are machine-local custody and never deleted by core, so evicting such a copy would strand the origin's obligation; that is why it is kept.
