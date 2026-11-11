# Check hooks select a supported Python without a virtualenv

Patch **2163**.

## Behavior

When no checkout virtualenv is available, the generated `scripts/check` hook tries the checkout launcher interpreter before scanning PATH for a Python at least 3.11. An older `python3` first on PATH no longer hides a usable newer interpreter. Existing virtualenv selection remains ahead of this fallback.

## How it works

The hook asks `.hermes/bin/hermes --print-runtime-command` in the common checkout and configured/default Hermes install for its JSON argv, decodes argv[0], and verifies its version. If neither launcher supplies a usable candidate, it searches executable paths for `python3.14`, `python3.13`, `python3.12`, `python3.11`, `python3` and `python`, in that order. A bare PATH Python is the final fallback, preserving the normal failure diagnostic if none satisfies 3.11. JSON decoding uses available `python3`, with a restricted shell extraction fallback.

## Code

`scripts/check`; regression additions in `tests/scripts/test_code_health_cli.py`.

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.** The test file installs and invokes a generated pre-commit hook in disposable repositories:

- `test_hooks_without_a_virtualenv_pass_over_a_path_python3_older_than_3_11`.
- `test_hooks_with_no_python_3_11_anywhere_still_run_the_bare_python3`.
- `test_hooks_without_a_virtualenv_take_the_launchers_interpreter`, parametrized over launcher placement/store cases.

These tests exercise hook execution, not a real push or production managed-runtime installation. No separate pre-push invocation test is added in this patch.

## Applying

Follow the full ordered replay in [README.md](README.md), with its replay plan and bridges. Do not apply the mail patch independently.

## Gotchas

The version floor is for this check-hook selection, not a promise that every Hermes runtime supports Python 3.11. A discovered interpreter still needs the check's dependencies. With no suitable Python at all, the hook still fails rather than bypassing checks.

## Plugin?

No. This is the repository's generated Git-hook bootstrap.
