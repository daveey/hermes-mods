# fix(state): a >60s state.db writer pauses a turn instead of ending it; name the lock holder on macOS [daveey]

Patch 0936, source `4ee467592cb9f229670a0cd7b162132f95dc2314`.

## Behavior

fix(state): a >60s state.db writer pauses a turn instead of ending it; name the lock holder on macOS [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from agent.transcript_repair import sync_flushed_message_markers`
- `class SessionPersistenceMixin:`
- `-"""Who holds the SQLite write lock on state.db right now (Linux /proc/locks).`
- `_MAIN_LOCK_NAMES = {`
- `def _describe_range(sidecar: str, start: int, end: int) -> str:`
- `def parse_proc_locks(text: str, inodes: dict[tuple[int, int], str]) -> list[tupl`
- `def state_db_write_lock_holders(db_path) -> list[str]:`
- `+"""A writer outliving SessionDB's 60s patience must pause, not kill, a tool turn."""`
- `+"""Darwin write-lock holder diagnostic.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A flush whose only failure is a busy writer now retries (up to 600 s, interrupt-aware) with the
> transcript held in memory; other persistence failures still fail closed. hermes_state_lockowners
> gains a Darwin F_GETLK child probe and stops claiming 'holder released' when it cannot look.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `agent/session_persistence.py`
- `hermes_state_lockowners.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_session_lock_retry.py`
- `tests/hermes_state/test_lockowners_darwin.py`

New or changed test definitions present in the source diff:

- `test_multistep_turn_survives_writer_longer_than_sixty_seconds`
- `test_non_lock_failure_still_fails_closed`
- `test_lock_retry_stops_when_interrupted`
- `test_lock_retry_gives_up_after_budget`
- `test_lock_retry_succeeds_once_writer_releases`
- `test_budget_defaults`
- `test_unprobed_detail_says_holder_unknown_not_released`
- `test_empty_completed_probe_still_says_holder_released`
- `test_probe_kind_is_linux_prefix_darwin_or_unsupported`
- `test_darwin_probe_offsets_are_wal_write_class_and_pending_reserved`
- `test_darwin_hits_name_each_write_class_byte_and_ignore_read_slots`
- `test_log_says_unknown_when_platform_has_no_probe`
- `test_probe_timeout_logs_unknown_not_released`
- `test_probe_garbage_logs_unknown_not_released`
- `test_darwin_spawn_passes_the_probe_plan_to_a_bounded_child`
- `test_live_subprocess_writer_is_named_and_files_are_not_written`
- `test_probe_does_not_drop_parent_sqlite_locks`
- `test_rollback_immediate_holder_is_named_reserved`
- `test_child_script_reports_only_the_offsets_it_is_given`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
