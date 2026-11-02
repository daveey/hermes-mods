# Shell-quoted interpreter path in shell command lines

Patch: `patches/1586-fix-tests-shell-quote-sys.executable-wherever-it-bec.patch` (`80b545c28d`, `fix(tests): shell-quote sys.executable wherever it becomes a shell word`, PR #581).

Related: [macOS signed Python](macos-signed-python.md), [macOS venv test fixtures](macos-venv-test-fixtures.md).

## Behavior

- Test fixtures and helpers that build a shell command string from `sys.executable` (and the script path next to it) now wrap both in `shlex.quote`.
- Before, an interpreter path containing a space, for example the app-bundle Python at `~/Applications/Hermes Python.app/Contents/MacOS/python3.14`, split at the space. `sh` then ran `.../Hermes` and exited 127. Some tests failed. Others passed hollowly: the process-consumer health tests launched zero fixture helpers and still passed.
- The Teams adapter's pip-install hint (`plugins/platforms/teams/adapter.py`) is the only product site. It now prints `shlex.quote(sys.executable) -m pip install`.
- Shebang lines (`#!{sys.executable}`) are exempt. The kernel splits a shebang at the first space before any shell runs, so quoting cannot help there. Argv lists and whole-string uses (the interpreter as the entire value) were already safe and are unchanged.

## How it works

- 12 shell-command sites are changed: 11 in tests (hook commands, `GIT_EDITOR`, wrapper scripts, helper commands) and the Teams adapter.
- `tests/test_shell_quoted_interpreter.py` is an AST guard over the repository's Python files. It skips `.git`, virtualenvs, `node_modules`, `__pycache__`, `website`, `apps`, `build`, `dist` and `.worktrees`.
  - It finds `sys.executable` (or `sys._base_executable`) inside an f-string or a `+` string concatenation.
  - It flags the use when the interpreter is a bare shell word: it starts the string or follows a space, tab, newline, `;`, `&`, `|`, `(` or a backtick, and whitespace follows it. A value wrapped in `shlex.quote(...)`, inside `'...'`/`"..."`, glued to other text (`ExecStart=...`), `!r`-formatted, or after `#!` is not flagged.
  - `_NOT_SHELL` lists the lines that name the interpreter in text no shell runs, keyed by (repo path, stripped line). It has one entry: the fake systemd `ExecStart` argv in `tests/hermes_cli/test_gateway_runtime_service.py`, which its fixture splits itself. A second test fails when an entry no longer matches a source line.

## Code

- `plugins/platforms/teams/adapter.py`
- `tests/gateway/test_process_consumer_health.py`, `tests/tui_gateway/test_process_consumer_health.py`
- `tests/tools/test_process_ownership_snapshot.py`
- `tests/hermes_cli/test_update_custody.py`, `tests/hermes_cli/test_linux_desktop_entry.py`, `tests/hermes_cli/test_gitlock.py`, `tests/hermes_cli/test_safe_mode.py`
- `tests/tui_gateway/test_served_profile_child_env.py`
- `tests/scripts/ci/test_upstream_sync.py`
- `tests/e2e/core/parity/_helpers.py`
- `tests/gateway/fixtures/kanban_owner_probe.py`
- New guard: `tests/test_shell_quoted_interpreter.py`

## Tests

In the patch; not run for this mirror. The counts below are from the source commit, run on macOS with the spaced Python 3.14.9 (the guard also on the core venv's 3.11.7).

- `tests/test_shell_quoted_interpreter.py`:
  - `test_detector_flags_a_bare_interpreter_word` (parametrized snippets)
  - `test_detector_allows_quoted_or_non_shell_text` (parametrized snippets)
  - `test_shell_lines_quote_the_interpreter`: the repo scan. On the parent it found 12 offenders (1 failed, 18 passed); with this commit, 19 passed.
  - `test_not_shell_entries_still_match_a_line`
- Process-consumer health tests: 0 fixture helpers launched before, 17 after (47 passed both ways).
- The 17 affected test files run serially: 24 failed before and 22 after, with no new failures. The two fixed are `test_upstream_sync::test_pre_push_guard_refuses_a_hand_pushed_sync_and_allows_fork_work` and `test_served_profile_child_env::test_helper_children_resolve_secrets_through_the_served_profile`. The commit reports that the 22 failing both ways contain no quoting site and fail the same way on the parent under that interpreter.
- Untested: the Teams adapter's install hint has no test of its own beyond the guard scan.

## Applying

Apply the full ordered series per [README.md](README.md) "Applying". The changes are one-line edits in otherwise independent files, but standalone application is not certified.

## Gotchas

- The guard checks Python sources only. Shell scripts, YAML workflows and `apps/` (Desktop) are not scanned.
- A shebang with a spaced interpreter path still breaks. The guard does not flag it, because no quoting can fix it; a fixture needing a script must call the interpreter explicitly instead.
- A new non-shell use of a bare `sys.executable` in a string (text no shell runs) needs a `_NOT_SHELL` entry with a reason.

## Plugin?

No. Test fixtures plus one product string in a core platform plugin.
