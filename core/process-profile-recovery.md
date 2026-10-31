# Per-profile background-job recovery

Patch: 1327.

## Behavior

A multiplexed gateway runs one `ProcessRegistry` for every profile, but each profile has its own `processes.json`. Before the fix every live job was written to every profile's checkpoint, and a finished job left only the file of the profile that saw its exit. Each later restart re-attached the finished job from another profile's stale copy and delivered another result (one job was re-attached by three profiles on successive hourly restarts).

Now:
- A job keyed `agent:<profile>:...` is written to, and recovered from, only that profile's checkpoint. In a multiplexed registry `agent:main` means the default profile; in a single-profile gateway `agent:main:` keys still recover in the named profile's own file.
- A non-owner that finds a foreign job hands the copy to the owner's checkpoint instead of re-attaching it.
- A job whose result receipt (`logs/process-results/<id>.json`) exists in any profile is never re-attached, and is dropped from every profile's `processes.json` when it finishes (one file lock held at a time).
- Multiplexed gateway startup first runs a one-time pass that settles finished copies and re-homes the rest.

## How it works

`tools/process_registry_profiles.py` (new) maps a session key to its owning profile (`owner_of`, `owned_here`), lists profile homes, and provides `result_recorded`, `drop_from_every_checkpoint`, `hand_to_owners` and `consolidate_checkpoints(multiplexed=)`. `_write_checkpoint` takes `settled_id`, which `_publish_finished` passes when a running job exits. `_recover_from_checkpoint_locked` returns foreign entries and settled ids; they are handled after its lock is released.

## Code

`tools/process_registry_profiles.py`, `tools/process_registry_checkpoint.py`, `tools/process_registry.py`, `gateway/run_startup.py` (sets `profile_scoped_checkpoints` and calls `consolidate_checkpoints` only when `multiplex_profiles` is on).

## Tests

- `tests/tools/test_process_checkpoint_profile_ownership.py::test_stale_copy_in_another_profile_is_not_reattached_after_the_owner_saw_the_exit`
- `::test_finished_job_left_in_another_profile_is_dropped_at_startup` (startup cleanup)
- `::test_non_owner_profile_never_reattaches_another_profiles_job` (hand-off to owner, re-attached once, other profile's writes never persist it)
- `::test_single_profile_gateway_keeps_recovering_its_own_main_keyed_jobs`
- `tests/tools/test_process_recovery_sandbox_reattach.py::test_a_reattached_sandbox_completion_starts_a_turn_in_the_live_gateway_session` (completion goes through batch, admission and wake injection to the live Slack adapter as an internal message)

Two existing tests only had their `_write_checkpoint` stubs widened to accept the new keyword. Untested: the Windows lock branch, and an owner profile whose home is absent on the host (job left where it is).

## Applying

Apply 1327. No config; behavior keys off the existing multiplex setting.

## Gotchas

Only `agent:<profile>:` keys are partitioned; a job with no profile in its key may still appear in any file. Cleanup is on startup, so stale copies persist until the next multiplexed gateway restart. No plugin involved.
