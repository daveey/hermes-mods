# Kanban worker requeue grants and crash records

Patches: 1162 1163 1164 1165 1166 1177

## Behavior

- Managed workers may persist registered `model_config` sidecars, including `model_routing`; an unregistered key is rejected before journaling, without latching a persistence failure. Class fallback continues if its session-row mirror fails.
- An owner-wake supervisor worker may use `kanban_unblock` for listed local-board cards in `triage` or `blocked`. Other workers and targets remain refused; the existing self-heal grant is retained.
- Crash output is restricted to the failed run, not an earlier reply in the append-only task log. Managed worker exceptions book a redacted cause that an unknown-execution refusal can quote.
- Deferred class selectors use provider `auto`, regardless of the frozen concrete provider. Managed replies are flushed before the exit trailer.

## How it works

`hermes_state_sidecar_keys.SIDECAR_KEYS` is shared by owner validation and the worker pre-check. Class fallback catches session-row mirror errors after changing its route.

`kanban_requeue.py` owns self-heal and owner-wake grants; `kanban.py` re-exports the moved names. An owner-wake grant requires `created_by='owner-wake'` and a title starting `Owner wake: supervise`. Only matching `- <board>/<id>` lines before the body's first blank line count. Successful tool unblocks emit `owner_wake_requeue` with the supervisor and target IDs.

`current_run_log()` removes a trailing current-run exit marker, cuts after the preceding marker, then starts at the last `Session: <id>` line when present. This also excludes previous replies written after their trailers by older clients. `_exit_code()` prints the reply with `flush=True`.

Worker failure booking collapses whitespace, redacts sensitive text and truncates the exception description to 300 characters. Start failures are marked to avoid duplicate booking; later failure booking is best-effort. `_deferred_class_runtime()` leaves class resolution to the agent and clears credential/transport fields.

## Code

- Sidecars: `hermes_state_sidecar_keys.py`, `hermes_state_worker_context.py`, `agent/runtime_session_store.py`, `agent/model_routing_agent.py`.
- Requeue: `hermes_cli/kanban_requeue.py`, `hermes_cli/kanban.py`, `tools/kanban_tools_self_heal.py`, `tools/kanban_tools_schemas.py`.
- Failures and routing: `agent/managed_worker.py`, `gateway/session_kanban.py`, `gateway/session_worker_construct.py`.
- Logs: `hermes_cli/kanban_worker_log.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_worker_client.py`.

## Tests

Tests present in the patches; not executed for this documentation change:

- `tests/hermes_state/test_sidecar_keys_registry.py`: AST writer-key drift guard, owner-reserved lineage exclusion, and shared owner/compaction registry.
- `tests/agent/test_managed_class_fallback_sidecars.py`: `test_class_route_quota_failure_fails_over_and_worker_persists_model_routing` exercises mocked HTTP 429 fallback with successful/refused mirrors; `test_unregistered_sidecar_key_fails_one_call_without_poisoning_the_journal` checks later writes still work. HTTP 402 on this managed-store path is untested.
- `tests/tools/test_kanban_owner_wake_requeue.py`: tool availability, listed-card unblocks and audit payload; refusals for unlisted/remote/other-board cards, disallowed statuses, wrong creator/title and plain workers; `test_requeue_grant_function` imports the extracted module after 1164.
- `tests/hermes_cli/test_kanban_crash_output_current_run.py`: silent failed runs, their own failure text, and first-run output, with/without a closing trailer.
- `tests/hermes_cli/test_kanban_worker_start_failure_cause.py`: redacted one-line startup cause, unknown-receipt quoting, late persistence failures, duplicate suppression, best-effort booking and `managed_worker.main()` exception handling.
- `tests/gateway/test_kanban_class_tier_route.py::test_route_runtime_leaves_a_class_to_the_agent_with_no_session_id`: frozen `anthropic`/`auto`, fallback enabled/disabled, no pinned key or URL. Added in 1165; 1166 supplies the corresponding runtime correction.
- `tests/hermes_cli/test_kanban_crash_output_interleaved_log.py`: old interleaved logs, first-run controls, and `test_client_writes_its_reply_before_its_exit_trailer`, a subprocess with stdout/stderr redirected to one file.
- Live gateway dispatch and live provider requests are untested here.

## Applying

Apply the complete numbered patch series at the fork/upstream merge-base with `git am -3 --empty=keep`, as described in [README.md](README.md). These entries are not independent patches; do not apply only this feature's numbers.

## Gotchas

- Requeue permission is an in-process tool grant, not a relaxation of the worker shell fence. Remote card lines and IDs mentioned after the first blank line confer no grant.
- Identity/lineage sidecars remain owner-reserved. Explicit launch credentials or a launch URL bypass deferred-class handling; this change does not authorize them for class selectors.
- Failure booking needs a validated bootstrap frame with Kanban context. Log isolation relies on exit trailers and, for managed-client interleaving, recognizable `Session:` lines.

## Plugin?

No, core change: it spans worker persistence, routing, exception handling, log extraction and Kanban tool authorization.
