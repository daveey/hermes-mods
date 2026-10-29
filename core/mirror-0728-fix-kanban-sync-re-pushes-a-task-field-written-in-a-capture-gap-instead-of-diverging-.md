# fix(kanban): sync re-pushes a task field written in a capture gap instead of diverging silently [daveey]

Patch 0728, source `c36b1ef5f03bfe1a2f7a678b68780cc119415429`.

## Behavior

fix(kanban): sync re-pushes a task field written in a capture gap instead of diverging silently [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import threading`
- `class RoundResult:`
- `def sync_board(slug: str, path: Path, hub: Hub, gateway: str) -> RoundResult:`
- `def diff(live: sqlite3.Connection, shadow: sqlite3.Connection, gateway: str) ->`
- `def test_rows_written_in_a_capture_gap_are_queued_on_the_next_install(live, kanb`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Hub incident 2026-10-28 (t_01a180daf776c25e): kanban_wake_check._wake moved a
> card scheduled -> ready while another process was re-installing the capture
> triggers. The next install's gap heal queued the uid-less task_events row (so
> the wake_check_broken event reached the service), but a tasks row has no
> "never captured" marker, so its status change was never queued. The service
> kept 'scheduled' and refused every lease as not_claimable for ~3 h, until a
> later local write overwrote the status.
>
> Each sync round now runs repair_divergence after the pulls: for this
> gateway's cards whose live sync_version equals the replica's and that have
> no pending or rejected tasks op, any synced field that still differs can only
> be an uncaptured local write. Exactly those fields are queued (base_version =
> the shared version) and a warning is logged; RoundResult.repaired lists them.
> Cards with unequal versions (a remote edit not yet applied) are left alone.
>
> Tests: the gap reproduction (event synced, status not) in shadow and primary
> mode, RED before / GREEN after, plus a control that an unpulled remote edit
> is not clobbered.

## Code

- `hermes_cli/kanban_sync_client.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_sync_client.py`

New or changed test definitions present in the source diff:

- `test_a_status_change_in_a_capture_gap_reaches_the_service_with_its_event`
- `test_divergence_repair_leaves_an_unpulled_remote_edit_alone`
- `test_rows_from_before_the_first_attach_are_left_to_import`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
