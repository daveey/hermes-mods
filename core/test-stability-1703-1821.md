# Test stability 1703–1821

Patches: **1708** (`567f99e00e`, `[daveey] Test backup busy deadline at its guarded opener boundary`); **1709** (`c058c7e3d3`, `[daveey] Split backup deadline regression into topical test file`); **1740** (`a36e78b224`, `test: make system unit home adoption fixture hermetic [daveey]`); **1741** (`79011327ab`, `test: split private socket preflight out of oversized service suite [daveey]`); **1748** (`a87ae5f6e2`, `[daveey] tests: bind synthesized-turn commit point into rebound prompt harnesses`); **1771** (`a4f63cd48b`, `test(kanban): headless worker probe passes the reservation's runtime generation`); **1786** (`5dfeba6e3f`, `test(gateway): webapp fallback peer picks fixed ports below the ephemeral range [daveey]`); **1787** (`944b3f9839`, `test(web): topology auth-gated test sets app.state.auth_required once`); **1805** (`52fd8afadb`, `fix(tests/ci): replay workflow steps under a bash 4+, not macOS /bin/bash 3.2 [daveey]`); **1813** (`3e6d257352`, `[daveey] test(kanban): full home tick with spawn budget never runs a worker-first queued card`); **1814** (`e6eb847f7b`, `test(gateway): stop command-discovery log polls clobbering the READY sentinel`); **1816** (`02d4edd800`, `test(kanban): pin the wall clock in board rows cache equality tests`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Backup busy deadline tested at its guarded opener (1708/1709); hermetic system-unit home fixture and split socket preflight suite (1740/1741); synthesized-turn commit point bound into rebound harnesses (1748); headless worker probe passes the reservation's runtime generation (1771).
- Webapp fallback peer uses fixed ports below the ephemeral range (1786); topology test sets `app.state.auth_required` once (1787); workflow steps replay under bash 4+ (1805); a full home tick never runs a worker-first queued card (1813); command-discovery log polls no longer clobber the READY sentinel (1814); board-rows cache equality tests pin the wall clock (1816).

## How it works

Test-only changes except `kanban_sync_client.py`/`machine_metrics.py` seams in 1813.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1708: (subject only)
- 1709: (subject only)
- 1740: (subject only)
- 1741: (subject only)
- 1748: CI shard 4 failed test_refused_input_commits_failed_mailbox_receipt with NameError: the harness rebinds _run_admitted_prompt onto an explicit namespace, and prompt_turn now reaches start_synthesized_turn via server globals (split-module import contract). Supply the production functions in both harnesses.
- 1771: (subject only)
- 1786: test_a_browser_credential_that_is_not_text_is_replaced_and_served flaked on CI (run 41547505172) with state 'unavailable': free_port() took a port from bind(0), closed it, and the peer bound it ~5 s later after its imports. With 16 parallel workers, other processes' bind(0)/connect() could take that ephemeral port in between, and the Webapp reported 'could not listen ... (Address already in use)'.
- 1787: pytest 9.2's monkeypatch.setattr reads the old value from the instance __dict__; Starlette's State keeps attributes in _state, so the test's second setattr (in a finally) also recorded "absent" and undo deleted twice: KeyError: 'auth_required' at teardown. One setattr, restored by monkeypatch, passes on pytest 9.0.3 and 9.2.0 (28 passed each).
- 1805: The replayed CI steps use mapfile (bash 4+: ci.yaml, tests.yml, catalog-tests.yml). _run took the first bash on PATH, which on a Mac is /bin/bash 3.2.57, so 17 test_update_ci_routing tests failed on every Mac with 'mapfile: command not found' while passing on Linux CI.
- 1813: Follow-up to #461 review. test_home_maintains_guard_hold_on_worker_queued_policy_card ticks with max_spawn=0, so its spawned == [] is trivial. The new test ticks the home with max_spawn=4 and a plain control card that does spawn, and asserts the worker-first-v1 card gets no spawn, no local or Board run row, no lease, and keeps its policy fence. local_admission is pinned True so only the policy fence keeps the card off. Parametrized both_fences (dispatch_refusals prefilter + claim gate) and claim_gate_only (prefilter disabled), so each fence is proven on its own.
- 1814: The test gave each child a 'w+' log handle and polled it with seek(0)+read(). The child inherits that open file description, so it shares the file offset: when the parent was throttled between its seek(0) and read(), the child's next line (' Hermes backend listening ...') landed at offset 0 over the HERMES_BACKEND_READY sentinel and the 40 s wait never matched it (CI log: ' Hermes backend listening on 127.0.0.1:37471 / RD_READY port=37471').
- 1816: test_board_payload_matches_a_cold_read and test_unchanged_board_materializes_rows_once compare whole board payloads read moments apart. Task ages are whole seconds of time.time(), so a throttled CI runner that straddles a second boundary between reads sees created_age_seconds differ (0 vs 1) and the equality fails. Freeze time.time() after setup so every compared read shares one clock second.

## Code

`hermes_cli/kanban_sync_client.py`, `hermes_cli/machine_metrics.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/ci/test_workflow_steps_bash.py`: `test_a_bash4_first_on_path_is_kept`, `test_a_bash4_in_a_known_dir_beats_a_bash3_first_on_path`, `test_a_bash_that_does_not_answer_is_not_selected`, `test_no_bash4_anywhere_skips_naming_the_bash_found`
- `tests/ci/workflow_steps.py`
- `tests/gateway/fixtures/webapp_fallback_peer.py`
- `tests/gateway/test_command_discovery.py`
- `tests/gateway/test_kanban_finite_admission.py`
- `tests/gateway/test_webapp_listener_fallback.py`
- `tests/hermes_cli/test_backup.py`
- `tests/hermes_cli/test_backup_busy_deadline.py`: `test_aborts_when_source_remains_busy_past_deadline`
- `tests/hermes_cli/test_gateway_service.py`
- `tests/hermes_cli/test_gateway_systemd_preflight.py`: `test_preflight_accepts_private_socket_without_dbus_bus`, `test_wait_for_user_dbus_socket_accepts_private_socket`
- `tests/hermes_cli/test_kanban_guard_hold_sync.py`: `test_full_tick_with_spawn_budget_never_runs_worker_queued_policy_card`
- `tests/hermes_cli/test_web_server_gateway_topology.py`
- `tests/plugins/test_kanban_board_rows_cache.py`: `test_board_payload_matches_a_cold_read`, `test_unchanged_board_materializes_rows_once`
- `tests/tui_gateway/test_bot_live_owner_delivery.py`
- `tests/tui_gateway/test_notification_admission_refusal.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
