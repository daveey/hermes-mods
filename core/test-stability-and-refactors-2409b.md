# Test deflakes and small refactors in the 2331–2409 range

Patches: **2332, 2355–2359, 2362–2363, 2376–2377, 2399–2400, 2402**.

## Behavior

These patches change little user-visible behavior. 2332 moves scalar helpers `_first_line`, `_opt_int` and `_json_or_null` into `kanban_db_values.py` to shrink the database facade and aligns queue/notice test contracts. 2357 fixes the Termux verifier so a descendant reaped between liveness and status probes counts as successful cleanup (an unobservable descendant is still reported).

## How it works

The remaining patches make tests deterministic: Xvnc argv capture synchronized (2355), scratch git fixtures isolated from background maintenance (2356), slow fanout writer awaited (2358), tmux server kept alive between clients (2359), hosted-member warning order (2362), lineage ordinal test isolated from prompt-worker lease races (2363), handoff exit observation across procfs reaping (2376), Docker skills refresh interval (2377), retired siblings in worker discovery (2399), onboarding admission settlement awaited before deletion (2400), completed pending ledger fold awaited (2402).

## Code

`hermes_cli/kanban_db.py`, `kanban_db_values.py`, `scripts/termux/validate_installed.py`; the rest are test files only.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_drain_wait_notice.py`, `test_kanban_missing_board_notice.py`, `tests/tools/test_kanban_unblock_queue.py`, `tests/scripts/test_termux_cleanup.py`, `test_termux_launchers.py`, `tests/tools/test_bot_desktop_launcher_xvnc.py`, `tests/hermes_cli/test_kanban_scratch_git_preservation.py`, `tests/tui_gateway/test_multi_client_fanout.py`, `test_tui_gateway_server.py`, `tests/hermes_cli/test_gateway_chat_native.py`, `tests/gateway/test_hosted_group_members.py`, `test_managed_worker_launch.py`, `test_onboarding_settlement_wait.py`, `tests/scripts/desktop_update/test_handoff_process_exit.py`, `tests/tools/test_docker_skills_mount_live.py`, `tests/hermes_cli/test_process_identity.py`. Flake reduction is not re-measured here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Deflake claims describe the fix intent in the patches, not a measured flake rate.

## Plugin

None.
