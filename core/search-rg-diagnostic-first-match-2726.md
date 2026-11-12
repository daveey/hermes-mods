# A cut-short rg diagnostic no longer swallows the first search match

Patches: **2724**.

## Behavior

- In the native `rg` search lane, `rg` is killed once the result bound is reached. `rg` writes each diagnostic (for example a permission-denied line) in several `write()` calls, so the kill can land mid-line. The kept stderr then ended without a newline and was prepended to stdout, fusing the fragment with the first match line. The parser read that fused line as a diagnostic, and a bounded search returned one result too few.
- Now an unterminated stderr tail is dropped when `rg` was killed (it names nothing reliably), and newline-terminated otherwise. The first match is no longer lost.

## How it works

`tools/file_operations_search.py`: the new `_kept_stderr_text(chunks, omitted, killed)` builds the kept stderr as newline-terminated lines, appends the "omitted diagnostics" line when some were dropped, and the search code prepends it to stdout. The search records `killed = proc.poll() is None` before terminating the process group and passes it in, replacing the earlier inline `rstrip` handling.

## Code

`tools/file_operations_search.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_search_unreadable_bound.py` (new `test_rg_killed_mid_diagnostic_keeps_the_first_match`; it also deflakes `test_many_unreadable_dirs_do_not_eat_the_result_budget`). Entry path: `ShellFileOperations.search(..., target="content", limit=3)` on the native lane (POSIX; skipped otherwise) with a fake `rg` script that prints a partial diagnostic to stderr, then five matches, then sleeps, so the cut is reproduced deterministically. The non-native search lanes and an unterminated tail with `rg` exiting on its own are **untested**. The commit message says the new test is red on the base and that the flaky variant failed once in CI; neither was reproduced here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A cut-short diagnostic is simply discarded when `rg` was killed, so that one unreadable path may go unreported.

## Plugin

Core change; no plugin required.
