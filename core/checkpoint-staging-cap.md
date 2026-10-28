# Checkpoints: vanished workspaces warn once; oversize files never reach git add

Patches: 0615, 0627 (daveey/hermes `48913b6339`, `4d28e047a7`).

## Behavior

0615: Kanban removes scratch workspaces on card completion, so a checkpoint can find its folder gone, even between the existence check and the git spawn. That now logs one WARNING instead of an ERROR traceback (a FileNotFoundError while the directory still exists keeps the ERROR), and `_take` skips pruning when the folder vanished after the snapshot landed. Scratch-workspace removal (completion and deferred parent cleanup) is logged at INFO with task id, last run id/status and the removing pid.

0627: the "skip files over `max_file_size_mb`" rule used to run only after `git add -A` had hashed the file, so one multi-GB file made every checkpoint time out, leave a partial `tmp_pack` and the index lock in the store, and be retried every turn. Now the cap is applied before staging, a staging timeout cleans its debris, and the folder is skipped for 60 minutes.

## How it works

New sibling `tools/checkpoint_staging.py`: one tree walk (replaces `_dir_file_count`) counts entries and collects files over the cap plus the 3 biggest; a per-project `core.excludesFile` of anchored literal patterns; timeout debris cleanup (tmp_pack_*, tmp_obj_*, index lock written since the call started; callers hold the store lock); a per-process 1 h back-off registry. `CheckpointManager._stage_all` runs `git add -A` with those excludes after unstaging tracked oversize paths (used by `_take`, `_diff`, `_safe_restore_plan`). `_unstage` uses `update-index --force-remove`. `_drop_oversize_from_index` stays as backstop.

## Code

- `tools/checkpoint_manager.py`, `tools/checkpoint_staging.py`, `hermes_cli/kanban_db_workspace.py`.

## Tests

- `tests/tools/test_checkpoint_manager.py`: `TestWorkingDirVanishes` (race, already gone, `_take` skips prune), `TestOversizeFilesNeverStaged` (50 MB file under a 1 MB cap never reaches the store; a file that grew past the cap is unstaged first), `TestGitTimeoutRecovery` (partial pack, loose temp and index lock removed; older foreign debris kept; next turn and other managers skip git add until the window ends; log names the folder and biggest files). `tests/hermes_cli/test_kanban_scratch_removal_log.py` covers the INFO removal line.

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- Excludes do not apply to already-tracked entries, hence the unstage step.
- The back-off is per process, so a restarted backend retries once.

## Plugin?

No. It changes core code that runs before or outside plugin loading.
