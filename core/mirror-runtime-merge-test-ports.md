# Test ports and fixture isolation across the upstream merge

Patches: 0987, 0989, 1018, 1023, 1025, 1026, 1029, 1030, 1032, 1036, 1038, 1041, 1045, 1047, 1049, 1057, 1115, 1122, 1126, 1129.

## Behavior

Test-only compatibility changes (one non-test side effect each is noted). The fork's tests are adapted to upstream's renamed seams, or upstream's tests to fork behaviour:
- Desktop vitest files (987): browser-defaults table, shortcut labels, fresh `kanban_observed_at`, connection mock exports, archive runtime snapshot, onboarding-decided ordering, `spawnPriority` dropped. The only non-test change passes a narrowing filter ignoring the automated default so empty profile sections show in the sidebar (`sidebar/index.tsx`).
- Agent tests (989): vendor-qualified model ids, `precommit_deadline_monotonic`, extracted-reasoning-only relay.
- TUI gateway tests (1018, 1023, 1025, 1026, 1030, 1032, 1036): retired bot-mailbox poll stub removed, owner-refusal backoff expectations, `report_ownership_refusal` kwarg, `take_ready` probe, running-job reaper pin, heartbeat submit stub, host-owner login subject.
- Others: cron bot-chat argv test removed (1029), orphan-recovery check reads `web_server_app` (1038), `gateway.run_bootstrap` pid-claim (1041), `--source` label stand-in (1045), delegate profile-route doubles (1047), `_age` shadowing (1049), nested-home identity (1057), lazy env registry lock-inversion (1115), CLI facade globals restored after reloads (1126), browser process isolation and TUI build fixture (1129).
- 1122 documents a hook-registration contract in `hermes_cli/plugins_snapshot.py` (comment only) and updates a test to expect `served True`.

## How it works

Each patch rebinds a stub or expectation to the post-merge name or behaviour. 1126 wraps `patch.dict(_cli_mod.__dict__)` around the mocked reload so `_pt_print`/`_PT_ANSI` bindings are restored. 1129 restricts `psutil.process_iter` to the test's own children and copies two TUI helper scripts into the miniature checkout. 1115 triggers the first `OPTIONAL_ENV_VARS` touch under `config_env_registry`'s lock and asserts no deadlock against journal-mode resolution. 1030 expects the fork's extra reaper pin (an owner of a running job stays pinned until the job exits).

## Code

- Desktop: `apps/desktop/electron/remote-warm-dispatch.test.ts`, `src/api/sessions.test.ts`, `src/lib/keybinds/browser-defaults.test.ts`, `browser-hints.test.tsx`, `src/store/native-notifications.test.ts`, `owner-queue-reconcile.test.ts`, `browser-tab-title.test.ts`, `components/onboarding/exhausted.test.tsx`, hooks tests under `app/session/hooks/`
- `tests/agent/test_provider_parity.py`, `test_local_prompt_progress.py`, `test_compression_stall_deterministic_fallback.py`, `test_pre_final_delivery_channels.py`
- `tests/tui_gateway/test_notification_admission_refusal.py`, `test_notification_refusal_backoff.py`, `test_kanban_notify_poller.py`, `test_update_admission_caller.py`, `test_notification_held_handoff.py`, `test_detached_process_notify_keepalive.py`, `test_heartbeat_wakes.py`, `test_resume_persisted_owner.py`, `test_notification_owner_refusal_backoff.py`, `test_process_consumer_health.py`
- `tests/hermes_cli/test_worker_argv_ignores_cwd_checkout.py`, `test_gateway_start_refused.py`, `test_chat_c_fail_loudly.py`, `test_windows_ssh_runtime_inventory.py`, `test_cli_interrupt_ack_race.py`, `test_cli_new_session.py`, `test_update_finish.py`, `test_plugin_hook_presence_record.py`
- `tests/tools/test_process_orphan_readopt_tui.py`, `test_delegation_profile_route.py`, `test_browser_real_profile.py`; `tests/pm/test_install_states_gc.py`; `tests/test_state_config_contention.py`

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. Names visible in the patches: `tests/hermes_cli/test_cli_new_session.py::test_make_cli_preserves_real_terminal_output` (new, 1126; the same name appears in `test_cli_interrupt_ack_race.py`); `tests/tools/test_browser_real_profile.py::test_the_browser_holding_a_dir_is_found_by_its_argv_never_a_helper` (1129, edited to take `monkeypatch`); `test_cron_bot_chat_turn_argv_imports_the_install` is deleted (1029). Everything else edits existing test bodies without adding names.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

These patches change expectations, so they hide a regression if the underlying fork behaviour is wrong; 987's message says tests were "ported, not weakened", which was not re-verified here. 1029 deletes a test because the lane it covered was removed upstream. They apply only on a base that has the renamed upstream seams.

## Could this be a plugin instead?

No: they are test-only (plus one comment and one sidebar filter argument).

## Follow-up patch (1497): browser-hint test imports at collection time

**1497** (`4c884bdd31`, `test(desktop): collect browser hint imports before behavior deadline`), test-only.

- **Behavior.** `apps/desktop/src/lib/keybinds/browser-hints.test.tsx` (the 0987-era test that the first-run tip, tooltips and keybind settings show the resolved browser binding, remaps included) no longer runs `vi.resetModules()` in `beforeEach` and six cold `await import(...)` calls inside its 15 s test body. It imports `KeybindSettings`, `TipBubble`, `TipKeybindLabel`, `I18nProvider`, `TIP_CATALOG` and `setBinding` statically. Its host setup (`localStorage.clear()`, `dataset.hermesDesktopHost = 'browser'`) runs in `vi.hoisted`, before those imports are evaluated. No product code changes.
- **How it works.** The keybind defaults read the host flag and saved bindings when their module initializes, so the host must be staged before the imports run. Before this patch, a cold renderer import inside the test could use up the behavior deadline. Collection-time imports move that cost outside the timed test, and `vi.hoisted` keeps the "browser host" input ahead of module initialization.
- **Tests.** The one test (`renders the resolved browser binding in the first-run tip, tooltips and settings, including remaps`) keeps its assertions, and its body is now synchronous. It was not run for this mirror.
- **Gotchas.** The module-level setup now runs once per file, not before each test. A second test added to this file that needs a different host or freshly loaded modules would have to reset them itself.
