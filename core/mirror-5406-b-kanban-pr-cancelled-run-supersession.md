# Kanban PR acceptance: a concurrency-cancelled run can be superseded by a later green run

Mirror entries 5166, 5167.

## Behavior

- A cancelled run (cancelled by a concurrency group when a newer event started the next run) previously left an `infra cancelled` row that kept `kanban_complete` refused after merge. `partition_at_merge` now drops a `cancelled` job only when Actions identity proves: same workflow id, event and head SHA; a different, later-created run that completed with success at or before the merge; a same-named job there that started before and succeeded by the merge; exactly one job of that name per run on both sides (unreadable same-name jobs count as siblings).
- 5167: the cancelled job itself must have terminal completion before the merge (completed_at present, not in progress); post-merge completion stays refused.
- Everything else stays fail-closed: failures/timeouts, no later run, non-success later runs, non-Actions apps, unreadable identity, 404s. The required-check path and open-PR path never read workflow runs.

## How it works

- `hermes_cli/kanban_pr_merge_time.py` (partition and proof), `kanban_pr_acceptance.py` and `kanban_pr_fallback.py` (resolver wiring).

## Code

- `hermes_cli/kanban_pr_merge_time.py`
- `hermes_cli/kanban_pr_acceptance.py`
- `hermes_cli/kanban_pr_fallback.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_pr_cancelled_superseded.py` (source coverage includes `test_cancelled_run_superseded_by_later_green_workflow_run`, `test_chain_of_cancelled_runs_each_superseded`, `test_earlier_failure_is_never_superseded`, `test_job_name_alone_is_not_proof`, `test_sibling_same_name_job_in_later_run_blocks_proof`, `test_open_pr_is_unchanged`, `test_required_path_never_reads_workflow_runs`; 5167 adds `test_cancelled_job_requires_terminal_premerge_completion`). Live GitHub API shapes are untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Without a workflow-run resolver, a cancelled job stays evidence.
- Job-name equality is never proof on its own.

## Why core instead of a plugin

The acceptance gate decides whether a core kanban completion is allowed; it cannot be loosened or tightened from outside.
