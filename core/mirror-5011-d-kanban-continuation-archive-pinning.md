# Kanban continue: verified portable archives suppress machine pinning

Patches: **4682, 4683, 4684, 4685, 4686**

- 4682 (`2de9338164`): [daveey] Preserve pool placement for verified hub continuation archives
- 4683 (`d3580ad150`): [daveey] Bind continue-archive comment window to the current run
- 4684 (`a1b06188e7`): [daveey] Bind portable continuation archives to current workspace bytes
- 4685 (`acedc3429f`): [daveey] Make archive comment boundary fixture deterministic
- 4686 (`77dba0119b`): fix(kanban): reject archive Git common-directory indirection [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- When a continued card's worker published a portable workspace archive, pool placement is preserved instead of pinning to the old machine, but only if verification passes: explicit portable declaration, task/run scoping, exact hub archive digest, and complete content/type/mode inventory equal to the current workspace (including Git HEAD and dirty bytes).
- The archive comment must postdate both the last move and the current run's start (strictly); an earlier run's comment does not carry to a later run.
- External trees, linked Git metadata (including `commondir` indirection) and unsupported filesystem entries fail closed to the conservative pin; diagnostic-only tarballs cannot grant portability. Local state is rechecked after the remote read.

## How it works

- `hermes_cli/kanban_continue_archive.py` performs the hub path and adjacent SHA256 checks outside the write transaction and returns whether to suppress only the pin operation; `kanban_continuation.py` calls it.
- `kanban_archive_inventory.py` builds the local and hub inventories and rejects unsafe entries; `docs/kanban-portable-workspace.md` documents the contract.

## Code

- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_continue_archive.py`
- `docs/kanban-portable-workspace.md`
- `hermes_cli/kanban_archive_inventory.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/test_kanban_archive_commondir.py`: `test_external_commondir_must_retain_pin`
- `tests/hermes_cli/test_kanban_archive_content.py`: `test_content_bound_continue`
- `tests/hermes_cli/test_kanban_archive_inventory.py`: `test_archive_inventory`, `test_local_inventory_fails_closed`
- `tests/hermes_cli/test_kanban_continue_archive.py`: `test_continue_archive`, `test_no_archive_still_pins`, `test_reference_scope`, `test_comments_after_move_and_no_old_fallback`, `test_hub_hash_verification`, `test_earlier_run_archive_comment_does_not_carry_to_next_run`, `test_comment_window_starts_at_current_run`, `test_hub_inventory_verification`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Missing, mismatched or stale archives must keep pinning.
- Timestamps compare strictly; same-second comments do not count.
