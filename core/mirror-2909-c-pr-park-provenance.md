# Parked PR checkpoint restatements preserve the handoff

Patches: **2870, 2900**.

## Behavior

An orderly parked worker can restate a previously published PR in its own checkpoint note without re-arming `active_pr`. A comment’s wake-evidence snapshot on a finished dependency likewise does not turn that dependency back into awaited review work.

## How it works

- **2870:** `restated_after_park` accepts only a single comment event written by the exact parking run, at/after scheduled/continued park and before any later claim. Every retained PR URL must already occur in the card’s pre-park PR comments. Synced schemas require portable `writer_run_uid`; unsynced schemas alone may use the local integer. Accepted notes are ordered before the park for the hold watermark without removing their PR URLs.
- **2900:** `_historical_parent_history` excludes `wake_episode_evidence` alongside comments, archive and late worker-result bookkeeping. Real later lifecycle activity continues to invalidate the historical-parent exemption.

## Code

- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_pr_park_note.py`
- `hermes_cli/kanban_dependency_review.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_kanban_pr_comment_provenance.py` checks scheduled/continued × local/synced restatements and controls for new PRs, foreign/missing/ambiguous writers and later claims. `test_kanban_pickup_event_history.py` checks pickup behavior. `test_kanban_dependency_review_history.py` adds a real comment path that emits wake evidence on a finished parent and leaves the respawn guard clear.

- `tests/hermes_cli/test_kanban_pickup_event_history.py`
- `tests/hermes_cli/test_kanban_pr_comment_provenance.py`
- `tests/hermes_cli/test_kanban_dependency_review_history.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

No PR identity, exact-head approval or publication guard is waived for a new PR, another run or unreadable provenance. Bookkeeping is ignored only for the historical-parent test, not globally removed from task history.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
