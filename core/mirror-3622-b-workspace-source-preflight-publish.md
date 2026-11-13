# Kanban workspace_source: ending preflight and branch publish

Patches: **3589, 3592**.

## Behavior
- Every voluntary ending of an attached run (complete, block, schedule, review handoff, continue) first runs a preflight and publishes the task branch; the ending carries the resulting receipt (`published`, `unchanged` or `retained`).
- Publishing requires: the card's own branch (not a protected or foreign one), a clean worktree, no private ignored files, no LFS or submodules, a remote that has not diverged, a lease still held, and a secret scan pass over new commits, messages and denylisted paths.
- An unchanged branch sends no publish. A refusal, the preflight's own timeout or any unexpected error never blocks the ending: custody stays `retained`. Involuntary timeouts send no receipt.
- A Board-side source refusal after the push is classified as retained; rejected pushes are classified.
- A two-clone scenario resumes at the exact branch and sha after the original worktree is deleted.
- The secret scan redacts regardless of the display redaction setting (3592).

## How it works
- 3589 adds `kanban_source_preflight.py` (computes the receipt for a run ending; never raises, anything unexpected becomes `retained`; stages the receipt for the single status-leaving UPDATE) and `kanban_source_publish.py` (branch scope, local state, remote proof, secret scan, lease renewal, push, Board manifest record; a per-preflight time budget caps every git call). It also touches `kanban_db.py` and `kanban_workspace_publication.py`.
- 3592 forces redaction in the scan and adds the publish tests.

## Code
- `hermes_cli/kanban_source_preflight.py`
- `hermes_cli/kanban_source_publish.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_workspace_publication.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_publish.py`: `test_publish_pushes_and_records_manifest`, `test_unchanged_sends_no_publish`, `test_publish_disabled_on_park_is_retained`, `test_dirty_worktree_refused`, `test_ignored_private_file_refused`, `test_secret_added_then_removed_across_two_commits_refused`, `test_secret_in_commit_message_refused`, `test_denylisted_rename_refused`, `test_secret_scan_ignores_display_redaction_setting`, `test_lfs_and_submodules_refused`, `test_foreign_branch_refused`, `test_protected_branch_refused`, `test_remote_diverged_refused`, `test_lease_lost_before_push_refused`, `test_publish_source_refused_is_retained_after_push`, `test_preflight_own_timeout_is_retained`, `test_preflight_never_raises`, `test_rejected_push_classified`, `test_complete_task_publishes_and_carries_published_receipt`, `test_publish_refusal_never_blocks_the_ending`, `test_scheduled_continue_carries_receipt`, `test_review_handoff_carries_receipt`, `test_involuntary_timeout_sends_no_receipt`, `test_two_clone_resume_exact_branch_and_sha_after_worktree_deleted`.
- Request-changes and move endings, and CLI/tool/Desktop entry paths, are untested here.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A publish refusal is deliberately non-fatal: the card ends normally but the source stays with the machine that has it.
