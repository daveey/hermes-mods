# Human asks that resolve outside Hermes (`human_request.resolved_check`)

## Behavior

A blocked card's `human_request` may carry `resolved_check`, a shell probe that exits 0 once the human's step has happened anywhere, and `resolved_check_every` (seconds, default 900, range 120–21600). The card then resumes when the step is done even if nobody clicks unblock.

- **Filing.** The probe runs once, synchronously, when the ask is filed (`bash -lc`, 30 s timeout), in the cwd and profile env the dispatcher will use for that card. Exit codes are read literally: exit 1 (not yet) files the ask; exit 0 refuses it as `ask_already_resolved` (the state already holds, so continue the work); anything else (exit ≥ 2, timeout, a probe that cannot start) refuses it as `resolved_check_broken` with the output tail. A probe containing a secret or credential-shaped value is refused as `resolved_check_secret` before it runs, because asks are stored redacted and the stored command would differ from the one that ran.
- **Sweep.** While the card stays `blocked`, the dispatcher's condition-wake sweep runs the probe with the same runner, concurrency cap and failure count as a park's `wake_check`. Its state row lives in `task_wake_checks` with `purpose='ask_resolved'`. One exit 0 appends an `ask_resolved` event, comments that the ask was satisfied outside Hermes, and unblocks the card the normal way (`ready`, or `todo` behind an open parent).
- **Failure limit.** Three failures in a row append `resolved_check_broken`, comment once and stop probing. A broken probe never unblocks the card, and it is not restored until the ask is filed again.
- A plain `wake_check` on a human block is still refused. The ask's probe goes in `human_request.resolved_check`.
- **Synced boards.** The gateway that filed the ask runs the probe and names itself as `probe_host` in the filing event. A worker node keeps its blocked copy while it runs that probe. A lost probe is restored once (`resolved_check_restored`, due at once); a second loss comments once (`resolved_check_lost`) and stops. Another gateway's probe is never started locally. The probing worker records `resolved_check_alive` at most every 3 h. When the home board hears nothing for that long plus two intervals plus 15 min, it comments once (`resolved_check_host_silent`). It never unblocks the card or starts a second probe. Permanently stopped probes take no write transaction.

## Entry paths

Filing runs on every path that can attach a human ask:

| Path | Test |
|---|---|
| `kanban_block` tool | `test_tool_block_files_ask_with_resolved_check`, `test_tool_block_refuses_resolved_or_broken_probe`, `test_tool_block_files_in_the_sweeps_cwd_and_env` |
| `kanban_create` tool (`initial_status="blocked"`) | `test_tool_create_blocked_files_or_refuses`, `test_tool_create_files_in_the_sweeps_env_without_the_creators_ids`, `test_tool_create_retry_with_idempotency_key_runs_no_probe` |
| `hermes kanban block --human-request` (including bulk `--ids`) | `test_cli_block_files_or_refuses`, `test_cli_block_files_in_the_sweeps_cwd_and_env`, `test_cli_bulk_block_checks_every_cards_context_before_blocking_any` |
| `hermes kanban create --human-request` | `test_cli_create_blocked_files_or_refuses`, `test_cli_create_files_in_the_sweeps_env_without_the_creators_ids`, `test_cli_create_retry_with_idempotency_key_runs_no_probe` |
| `hermes kanban block --metadata-only --human-request` | `test_cli_metadata_only_adds_changes_and_removes_check` |
| Dashboard `POST /tasks` (create) | `test_dashboard_create_refuses_resolved_or_broken_probe`, `test_dashboard_create_pending_and_idempotent_retry` |
| Dashboard `PATCH /tasks/{id}` (block and metadata-only) and bulk updates | `test_dashboard_patch_block_refuses_before_any_change`, `test_dashboard_metadata_only_refuses_a_met_probe`, `test_dashboard_bulk_checks_each_card_in_its_own_context` |

A create runs the probe before the card exists, with the target board pin, the assignee's env and the workspace cwd, but no `HERMES_KANBAN_TASK` (never the creator's ids). A create whose `idempotency_key` returns an existing card runs nothing. A bulk block checks every card in its own context before blocking any of them.

## How it works

- `hermes_cli/kanban_ask_resolved.py` (new): validation, the filing probe, the sweep step, restore/lost/alive/silent bookkeeping.
- `hermes_cli/kanban_human_request.py`: `resolved_check` / `resolved_check_every` fields, `RESOLVED_CHECK_MAX_CHARS` server-side length limit, secret refusal.
- `hermes_cli/kanban_wake_check.py`: the sweep runs `purpose='ask_resolved'` rows next to scheduled wake checks; `_profile_home` became `(assignee, task_id=None)` so a create can resolve the assignee's env before a task id exists.
- `hermes_cli/kanban_db.py`, `kanban_db_connect.py`, `kanban_db_creation.py`, `kanban_human_request_migration.py`: the `purpose` column on `task_wake_checks` (legacy tables gain it) and stale-row cleanup on archive or manual unblock.
- `hermes_cli/kanban.py`, `kanban_parser.py`: CLI paths. `tools/kanban_tools.py`, `tools/kanban_tools_schemas.py`: tool paths and schema. `hermes_cli/kanban_worker_node.py`: synced-board probe ownership.
- `plugins/kanban/dashboard/plugin_api.py`, `plugin_api_human_request.py`: dashboard PATCH, bulk and create checks.
- `website/docs/user-guide/features/kanban.md`: user documentation with device-button and Settings-toggle examples.

## Code

- Patch **1479** (`f73a1f8466`): the feature on all CLI/tool/dashboard-PATCH paths, sweep and sync.
- Patch **1480** (`d046b3821c`): dashboard create also runs the filing probe.
- Patch **1482** (`5ada37487f`): `test_kanban_pr_wake.py` fixture patches `_profile_home` with its new `(assignee, task_id)` signature (1479 had broken two tests there).
- Patch **1484** (`b41abac074`): dashboard create imports `plugin_api_human_request` absolutely. The dashboard loader runs `plugin_api.py` as a top-level module, so the relative import from 1480 raised on every `POST /tasks`. The `resolved_check` schema drops `maxLength` (llama.cpp's grammar converter rejects it); `RESOLVED_CHECK_MAX_CHARS` still enforces the limit server-side.
- Merged through PR #493 (merge `c21fe3290319da26cf0ebfa563e53aad7ff0ac2c`).

## Tests

- `tests/hermes_cli/test_kanban_ask_resolved_check.py`: the entry-path table above, plus literal exit-code reading at filing and in the sweep, filing timeout = broken, a sweep exit 0 that runs the probe once, unblock to `todo` behind an open parent, stale rows dropped on archive/manual unblock, scheduled wake checks still waking next to ask rows, broken probe comments once and never unblocks, legacy `task_wake_checks` gaining `purpose`, padded probes stored exactly as run, and `probe_host` on a synced board.
- `tests/hermes_cli/test_kanban_ask_resolved_sync.py`: relayed ask keeps one live probe and its unblock reaches the home board; a relayed ask without a probe is still evicted; alive reports; one silent-host comment; restore once then comment once; exhausted probe never restored until re-filed; a changed stored ask moves the row; a permanently stopped probe takes no write txn.
- `tests/hermes_cli/test_kanban_pr_wake.py`: the 1482 fixture correction.
- Exact PR head `b41abac0746f0506a62bfcc8baf88da2a6bee2d8` passed CI run `41294368192` before the normal merge.

**Untested:** the two 1484 fixes have no dedicated regression test. The dashboard tests import `plugins.kanban.dashboard.plugin_api` as a package, so they pass with either import form and do not exercise the top-level loader. Nothing checks for the absence of `maxLength`.

## Applying

Use the bridge-aware full-series recipe in [README.md](README.md), policy/replay snapshot 1484. Patches 1479–1482 and 1484 come from a branch based on an older fork commit. Applied after 1478 and 1483, they replay cleanly with `git am -3`.

## Gotchas

- Exit 0 at filing is a refusal, not success: the agent should continue the work instead of asking.
- A probe that needs its own card id cannot run at create time (no `HERMES_KANBAN_TASK`). File it with `kanban block` after the create.
- A broken probe stops after three failures and stays stopped until the ask is filed again. The card stays blocked for the human.
- The production-board refusal shipped alongside this feature is documented separately in [kanban-production-board-guard.md](kanban-production-board-guard.md).

## Plugin?

No. Filing runs inside core's block/create transactions and the dispatcher sweep. The public `user-steps` plugin separately retires matching asks after a resolved-check unblock (already published).
