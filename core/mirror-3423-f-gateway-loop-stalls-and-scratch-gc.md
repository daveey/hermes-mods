# Gateway event-loop stalls and kanban scratch GC

Patches: **3254, 3289**.

## Behavior
- The gateway stops doing per-request filesystem work on the event loop: the plugin consent gate re-sweeps plugin directories at most every 5 s and answers from a cached list (due sweeps run on a worker thread); home comparisons use memoized home keys; the control-socket peer check reuses a home's "no symlinked component" verdict for 30 s (passes only), while the home is still lstat'ed per connection so a swap to a symlink is refused at once and a symlinked-ancestor refusal is never cached.
- `kanban gc` holds the board write lock only for the candidate SELECT; walks and removals run outside it, each removal re-checks ownership in a short transaction (a card resumed or repointed mid-GC keeps its workspace) and detaches the tree by rename before deleting.
- A stale detached `.<id>.gc-rm-<hex>` tree whose delete failed is swept on later runs (never a symlink, only names GC mints) and no longer reported as unowned; an unreadable board at the candidate SELECT logs "Scratch GC aborted" and returns 0.
- The home-key memo moved to `hermes_constants.resolved_home_str`, so `reset_hermes_home_key_cache()` clears it too.

## How it works
- 3254 touches `web_server`, `control_socket`, `env_loader`, `secret_scope` and rewrites `gc_task_scratch` around short transactions. 3289 adds the trash sweep, moves the SELECT back inside the try, and relocates the memo.

## Code
- `hermes_cli/web_server.py`
- `gateway/control_socket.py`
- `hermes_cli/env_loader.py`
- `agent/secret_scope.py`
- `hermes_cli/kanban_worktree_gc.py`
- `hermes_constants.py`
- `gateway/AGENTS.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_control_socket_peer_realpath_cache.py`: `test_repeat_peer_check_reuses_the_home_realpath`, `test_home_swapped_for_a_symlink_is_refused_at_once`, `test_symlinked_ancestor_refusal_is_not_cached`.
- `tests/hermes_cli/test_kanban_scratch_gc_lock_scope.py`: `test_board_writable_while_scratch_gc_removes_trees`, `test_card_resumed_mid_gc_keeps_its_workspace`, `test_owner_repointed_mid_gc_keeps_the_tree`.
- `tests/hermes_cli/test_kanban_scratch_gc_trash_sweep.py`: `test_stale_detached_tree_is_swept_and_not_reported`, `test_sweep_leaves_lookalike_names_alone`, `test_undeletable_trash_is_reported_as_detached_not_unowned`, `test_board_writable_while_stale_trash_is_swept`, `test_unreadable_board_at_candidate_select_aborts_with_log`.
- `tests/hermes_cli/test_loop_fs_request_paths.py`: `test_reset_also_forgets_the_env_loader_home_key`, `test_env_loader_key_keeps_its_no_expanduser_semantics` (the plugin-gate caching from 3254 is covered here only as far as the patch's file list shows; individual names not recorded).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The peer-realpath cache trades a 30 s window for the ancestor-symlink check; only the home's own lstat is re-run per connection.
