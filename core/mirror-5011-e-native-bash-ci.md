# Native Bash compatibility coverage

Mirror entries: 4787, 4788, 4789, 4790.

## Behavior

Lifecycle shell changes select a native macOS Bash 3.2 premerge lane while scanner assertions remain active even when a modern-Bash execution premise must be skipped.

## How it works

Change classification includes scanner, execution and bootstrap dependencies, including the shared Bash test helper. A same-repository selected lane feeds the required-results consumer and excludes fork PR execution on the custom runner. A pytest-process guard verifies the native interpreter after bootstrap in every worker. Workflow lint recognizes the dedicated runner label. Scanner-contract tests are separated from interpreter-gated execution proofs; isolated fixture executables model missing GNU timeout/copy argv features without removing parent subprocess deadlines. Bash-3 syntax rejection receives an executing balanced-pattern twin.

## Code

- `.github/actionlint.yaml`
- `.github/actions/detect-changes/action.yml`
- `.github/workflows/README.md`
- `.github/workflows/ci.yaml`
- `.github/workflows/hermes-state-macos.yml`
- `.github/workflows/macos-bash32.yml`
- `scripts/ci/bash32_guard.py`
- `scripts/ci/classify_changes.py`
- `scripts/ci/required_results.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

CI routing tests cover dependency selection, required-result wiring, fork/main-push behavior, interpreter resolution/version and worker guard loading. Comment, redirection and subscript suites retain ungated security assertions; compound sink, generated-script historical fixtures and session-alias controls adapt native execution premises.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/ci/test_classify_changes.py`
- `tests/ci/test_macos_bash32_routing.py`
- `tests/cron/test_lifecycle_guard_comment_words.py`
- `tests/cron/test_lifecycle_guard_compound_output_sink.py`
- `tests/cron/test_lifecycle_guard_fd_redirections.py`
- `tests/cron/test_lifecycle_guard_generated_scripts.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`
- `tests/cron/test_lifecycle_guard_subscript_sink.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The lane needs correctly labeled custom macOS capacity. Fixture portability does not prove native CI succeeded in this documentation task. The later containment entry removes the generated-script feature and its tests; native compatibility is not evidence that reverted protections remain active.
