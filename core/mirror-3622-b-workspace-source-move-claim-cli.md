# Kanban workspace_source: move, reclaim, CLI claim, source-release and docs

Patches: **3590, 3593–3594, 3598, 3601, 3603–3604**.

## Behavior
- Workspace-bound (`dir`) cards never move. A worktree source card moves only with portable custody (published or unchanged at the manifest revision); a parked move whose holder is local needs a clean local source at the commit. A running move publishes first and checks the fresh result; a publish failure keeps the run. A disabled host keeps the worktree bound.
- Reclaim refuses source cards; the CLI claim refuses cards under custody (tested at the command-function and parser level). A portable worktree card does not need a host checkout on its target.
- `hermes kanban source-release <task> --to published` (Board admin key) first verifies the remote branch head equals the manifest commit; its refusals and parser are tested.
- `SOURCE_PROTOCOL_COMPLETE` is flipped, so `enabled: true` is honoured (default stays off).
- On a board that never synced and so lacks the source columns, claim, move/reclaim and source-release read the absent columns as NULL instead of failing.
- The Kanban user guide gains a "Portable worktree sources" section (lifecycle, publishing, moving, freeing a stuck source); docs are not covered by tests.

## How it works
- 3590 adds `kanban_source_move.py`, `kanban_source_cli.py`, capability changes and parser entries, and flips the protocol flag.
- 3593 adds the move/CLI tests and stops push from setting an empty remote refspec.
- 3594 adds the docs and a config comment.
- 3598 builds the ls-remote environment through `build_subprocess_env`.
- 3601 and 3603 only keep `hermes_cli/kanban.py` within its line cap: wiring is folded, and `_cmd_claim` moves unchanged into `kanban_claim_cli.py`, re-exported as `kanban._cmd_claim`.
- 3604 adds `source_columns()` in `kanban_workspace_source.py`, which selects NULL for an absent column.

## Code
- `hermes_cli/kanban_source_move.py`
- `hermes_cli/kanban_source_cli.py`
- `hermes_cli/kanban_claim_cli.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_capabilities.py`
- `hermes_cli/kanban_runs_on_capabilities.py`
- `hermes_cli/kanban_workspace_source.py`
- `hermes_cli/kanban_workspace_source_config.py`
- `hermes_cli/kanban_source_publish.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_move.py`: `test_workspace_bound_cards_never_move`, `test_parked_move_needs_portable_custody`, `test_parked_move_with_portable_custody_allowed_elsewhere`, `test_parked_move_holder_needs_clean_local_source_at_commit`, `test_disabled_host_keeps_worktree_bound`, `test_move_check_uses_source_gate`, `test_running_move_publishes_then_checks_fresh_result`, `test_running_move_publish_failure_keeps_run`, `test_reclaim_refuses_source_cards`, `test_portable_worktree_is_not_host_only`, `test_cli_claim_custody_card_refused`, `test_cli_claim_command_refuses`, `test_source_release_verifies_remote_head_first`, `test_source_release_refusals`, `test_source_release_parser`, `test_unsynced_board_without_source_columns_claims_and_moves`.
- `tests/hermes_cli/test_kanban_workspace_source.py`: `test_enabled_honoured_once_protocol_complete`.
- 3598, 3601 and 3603 have no tests of their own (env construction and line-cap refactors).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- `kanban.py` is held at a 2000-line cap; further growth there may force more extractions.
- The Board refuses `source-release` while a lease is live.
