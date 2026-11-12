# Core-update pauses respect gateway and compression boundaries

Patches: **2871, 2872, 2873, 2874, 2875, 2905, 2906**.

## Behavior

Update checkpoints accept a wrapper unit’s actual gateway child, local automation wakes can park when their result contract permits it, and eligible long compressions yield before commit so the pause reaches a safe step boundary. A live writer’s closed paused record is retried in place rather than abandoned.

## How it works

- **2871:** checkpoint ACK PID may be the discovered unit PID or a `gateway run` descendant; nonce/checkpoint/drain validation remains. Card workers and unrelated gateways are not accepted.
- **2872:** add `gateway_service_members.py` to CI’s updater dependency selector for `e2e_upgrade`.
- **2873:** allow a well-formed local-route producer wake under the pause ingress contract, but exclude API, detached routes and noted completions. Publish an explicit boolean `update_pausable` on every active-work unit; non-chat units default false.
- **2874:** register compression as an in-flight cancellable call, poll a yield check, cancel only before the commit fence and return the unchanged transcript without timeout fallback/cooldown. Extract host wait helpers while preserving their re-exports. An admitted commit still completes.
- **2875:** restrict yielding to `post_tool`/`pre_api` compressions with a final tool-result message. Overflow recovery and turn-start/idle/engine preflights must finish their normal compression, not resend an over-window transcript unpaused.
- **2905:** closed/finalized records are not “user moved on” while this process still owns their pause marker. On window end, reopen from stored source using `session.resume`; retry refusal each poll, retain stuck warnings, and end the watch if the chat is in no store or another writer owns it.
- **2906:** test-only follow-up pins started-checkpoint precedence under notifier hold, conflict cancellation/vanish successor selection, and no spin when only a held notifier wake remains.

## Code

- `hermes_cli/update_cmd_posix_pause.py`
- `scripts/ci/classify_changes.py`
- `gateway/run_active_work_verb.py`
- `gateway/session_update_pause_ingress.py`
- `agent/compression_facade.py`
- `agent/conversation_compression.py`
- `agent/conversation_compression_wait.py`
- `agent/update_pause_cancel.py`
- `tui_gateway/update_pause.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_update_checkpoint_wrapper_pid.py` uses a real wrapper process tree. `test_update_pause_local_wake.py` composes admission → boundary park → same-admission resume; ingress and active-work verb tests check exclusion/publication. `test_update_pause_compression.py` covers precommit yield, admitted commits, Stop and overflow controls. `test_update_pause_closed_session.py` covers reaped records, failed reopen retry, takeover and missing-store exit. `test_conflict_cancel_successor.py` and `test_notifier_checkpoint_precedence.py` pin the notifier drain branches. Patch 2872 adds no separate test.

- `tests/hermes_cli/test_update_checkpoint_wrapper_pid.py`
- `tests/gateway/test_active_work_verb_update_pausable.py`
- `tests/gateway/test_update_pause_ingress.py`
- `tests/gateway/test_update_pause_local_wake.py`
- `tests/tui_gateway/test_update_pause_compression.py`
- `tests/tui_gateway/test_update_pause_closed_session.py`
- `tests/gateway/test_conflict_cancel_successor.py`
- `tests/gateway/test_notifier_checkpoint_precedence.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

Compression cannot be abandoned after commit admission. A fallback directory or generic child PID is not update authority. A marker owned by another process is not resumed here. CI routing is selection only, not evidence the native updater suite ran. Live Desktop/Kanban update timing is not verified by this mirror.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
