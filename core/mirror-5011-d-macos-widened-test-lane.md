# CI: macOS lane runs unmarked tests; cross-platform fixture repairs

Patches: **4703, 4704, 4705, 4706, 4707**

- 4703 (`bf9b477037`): [daveey] Measure marked, widened and cron macOS test selections on probe branch
- 4704 (`9615dc7750`): [daveey] Repair cross-platform premises exposed by widened macOS measurement
- 4705 (`7847af5810`): [daveey] Probe widened macOS lane with native CPU stub removed
- 4706 (`d1dcbfe266`): [daveey] Restore Darwin CPU isolation after branch mutation probe
- 4707 (`31122d738c`): [daveey] Run unmarked unit tests in macOS-selected files

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Both macOS selectors run unmarked unit tests in selected files, preserving Windows filtering and the separate GUI-session lane; temporary measurement matrix removed.
- Cross-platform repairs: host-independent missing-sqlite and empty-git fixtures, resolved noindex cache paths, isolated Whisper CPU policy, signing only Darwin Python payloads, central gating of modern system-Bash premises with a bounded fixture timeout wrapper.
- Darwin CPU isolation in the loop-accounting test is restored after a probe.

## How it works

`.github/workflows/macos-os-tests.yml`, `tests-os.yml`, `pm/packages.py`, new `tests/cron/bash_execution.py`, and assorted test files.

## Code

- `.github/workflows/macos-os-tests.yml`
- `pm/packages.py`
- `.github/workflows/tests-os.yml`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/ci/test_macos_os_tests_workflow.py`: `test_reusable_lane_preserves_windows_and_widens_only_macos`, `test_unmarked_darwin_regression_is_collected_in_widened_lane`
- `tests/cron/bash_execution.py`: modified only
- `tests/cron/test_bash_execution.py`: `test_version_gate`, `test_version_probe_is_cached_and_uses_system_bash`, `test_shared_marker_helpers_gate_before_execution`, `test_original_case_substitution_premises_use_shared_native_gate`
- `tests/cron/test_lifecycle_guard_comment_corpus.py`: modified only
- `tests/cron/test_lifecycle_guard_comment_words.py`: modified only
- `tests/cron/test_lifecycle_guard_fd_redirections.py`: modified only
- `tests/cron/test_lifecycle_guard_subscript_sink.py`: modified only
- `tests/hermes_cli/test_loop_accounting.py`: modified only
- `tests/hermes_cli/test_session_recovery_lost_and_found.py`: modified only
- `tests/hermes_cli/test_update_interrupted_pull.py`: modified only
- `tests/pm/test_stage_only.py`: `test_python_signing_requires_darwin_host_and_payload`
- `tests/scripts/test_bundle_native.py`: modified only
- `tests/tools/test_transcription_tools.py`: modified only

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- 4703, 4705 and 4706 are measurement probes whose net effect is folded into 4707 and the loop-accounting test.
- Net behavior change in production code is limited to `pm/packages.py` signing.
