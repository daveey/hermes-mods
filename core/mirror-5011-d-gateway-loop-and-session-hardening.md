# Runtime hardening: loop accounting names, registry finalizers, claim reads, MCP loop readiness

Patches: **4673, 4678, 4699, 4700, 4708**

- 4673 (`a71807ae54`): fix(loop-accounting): name direct gateway pool submissions [daveey]
- 4678 (`5a72af2525`): [daveey] Avoid registry imports from SessionDB finalizers
- 4699 (`da9b94562b`): fix(gateway): keep claim publication reads off owner loop [daveey]
- 4700 (`38b26a4fba`): fix(gateway): preserve lazy launch-source freezing during claims [daveey]
- 4708 (`96805b87ee`): fix(mcp): await loop readiness before first discovery [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Direct gateway `run_in_pool` submissions are attributed to a function-named scope in loop accounting when the registry is enabled.
- `SessionDB` finalizers no longer import the registry (which could hang on the global import lock after module teardown); release is bound when the registry grants ownership.
- Claim publication reads happen off the owner loop: execution is stamped on-loop, storage is prepared off-loop without the stream lock, and publication is fenced against a newer view; lazy launch-source freezing is preserved.
- The MCP loop thread signals readiness through a loop callback before first discovery is admitted; failed starts close the unpublished loop and late starts are reaped.

## How it works

`hermes_cli/local_work_threads.py`, `hermes_state.py` + `hermes_state_registry.py`, new `gateway/session_claim_publication.py` with `session_authority.py`/`session_queued_controls.py`, and `tools/mcp_tool_loop.py`.

## Code

- `hermes_cli/local_work_threads.py`
- `hermes_state.py`
- `hermes_state_registry.py`
- `gateway/session_authority.py`
- `gateway/session_claim_publication.py`
- `gateway/session_queued_controls.py`
- `tools/mcp_tool_loop.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/gateway/test_adversarial_settlement_review.py`: modified only
- `tests/gateway/test_owner_loop_claim_read.py`: `test_claim_publication_rechecks_a_newer_view`, `test_claim_preserves_lazy_launch_source_freeze`
- `tests/gateway/test_wake_admitted_before_drain_authority.py`: modified only
- `tests/hermes_cli/test_loop_accounting_residual.py`: `test_gateway_direct_pool_submission_is_named`
- `tests/hermes_state/test_registry_finalization.py`: `test_shared_db_finalizer_exits_without_import_lock`, `test_close_releases_shared_refs_without_importing_registry`
- `tests/tools/test_mcp_loop_startup.py`: `test_first_call_waits_until_loop_can_accept_work`, `test_failed_thread_start_closes_unpublished_loop`, `test_readiness_failure_reaps_late_start`, `test_ready_callback_does_not_publish_an_already_stopped_loop`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Finalizer fix preserves nonshared close, WAL retirement and refcount teardown.
- The fence must reject stale views; a stale-fence mutant fails many interleavings.
