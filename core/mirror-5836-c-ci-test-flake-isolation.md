# Test-harness hardening: bash-keyed replay memo, holder readiness, relative-home isolation, immutable race runner (mirror 5641–5643, 5664)

## Behavior
- The CI workflow-step replay memo is keyed on the bash binary selected for the step, so selection is repeated when PATH or known-bash-dirs changes and a different selected binary gets a separate entry and the 'no bash >= 4' skip still fires.
- Holder-argv tests wait for shell readiness before describing a child's command line.
- Gateway authority tests that bind a symbolic profile id run in a temporary cwd, so late owner-loop tasks cannot seed a Hermes home inside the checkout.
- The kanban create-overlap race test passes its runner source via argv instead of rewriting a shared file.

## How it works
The bash is selected before the memo lookup (selection is itself memoized per PATH and known dirs). Process spawn does not guarantee argv publication, so the holder fixture blocks on stdout readiness. The `authority_cwd_in_tmp` fixture is applied module-wide or per parametrized test. The race runner is passed with `python -c` so concurrent spawns never expose a truncated file.

## Code
- `tests/ci/workflow_steps.py`
- `tests/ci/test_workflow_steps_bash.py`
- `tests/hermes_cli/test_doctor_fts_deferral_holders.py`
- `tests/gateway/test_bot_mailbox_async.py`
- `tests/gateway/test_discard_delivers_captured_answer.py`
- `tests/gateway/test_local_turn_origin_execute_code_gate.py`
- `tests/gateway/test_notifier_core_drain.py`
- `tests/tools/test_kanban_create_overlap_race.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/ci/test_workflow_steps_bash.py::test_the_memo_still_serves_one_bash_and_reselects_another` counts replay spawns. `tests/hermes_cli/test_doctor_fts_deferral_holders.py::test_non_python_holder_waits_for_child_readiness` gates argv availability on stdout acknowledgement and checks cleanup. `tests/tools/test_kanban_create_overlap_race.py::test_spawn_keeps_pending_child_source_intact` gates child execution and checks nonempty output from both spawns. The four gateway files under Code add the existing `authority_cwd_in_tmp` fixture, not new delivery guarantees. Production runtime entry paths are unchanged and untested by these harness-only regressions.

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files. Test-only.

## Gotchas
- Test-only: no runtime behavior changes.
- Replay memo identity includes the selected bash path, not the full ambient PATH; a changed search setting can still resolve to the same memo entry.

## plugin?
No. Core change.
