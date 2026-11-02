# Background commands use Bash after login-shell initialization

Patches: 1616 1617

- `patches/1616-fix-terminal-background-commands-run-under-the-foreg.patch`
- `patches/1617-tests-follow-bg-shell-exec-change-patch-target-SIGTE.patch`

## Behavior

- **1616:** POSIX local background commands launched by `ProcessRegistry.spawn_local` run with Bash word splitting and `PIPESTATUS` semantics after login-shell initialization, even when the selected login shell is sh or zsh. The foreground `LocalEnvironment.execute` probe retains the same behavior.
- **1616:** Login-profile exports reach the command, but the temporary `HERMES_BG_COMMAND` variable is unset before the command runs. Registry polling and killing remain covered by a real-process regression test.
- **1616:** Both pipe and PTY spawn paths carry the rewritten command in the subprocess environment; existing mocked spawn assertions are adjusted to inspect it there. Actual PTY Bash semantics are untested by these patches.
- **1617:** Test-only follow-up moves the login-shell PATH-preservation mock to the new helper module and makes the late-child cleanup fixture explicitly ignore SIGTERM instead of relying on an interactive wrapper's signal behavior.

## How it works

`tools/process_registry_bg_shell.py` centralizes `bg_login_argv` and `bg_command_env`. On POSIX, the environment carries the command text in `HERMES_BG_COMMAND`. The login shell runs with `-lic`, disables job control, copies the text to `_hc`, unsets the transport variable, and executes the Bash selected by the foreground environment's `_find_bash` using `-c "$_hc"`. The Bash executable path is shell-quoted; command text is not interpolated into the login-shell script.

`ProcessRegistry._scope_argv` uses this helper before the existing optional systemd-scope wrapping. Both local pipe and PTY environments receive the command. For pipe execution with durable logging, the transported text includes the existing exit-recording wrapper. Windows retains its direct command-in-argv branch; this compatibility branch is untested by the added probes.

## Code

**1616:**

- `tools/process_registry_bg_shell.py`
- `tools/process_registry.py`
- `tests/tools/process_registry_fixtures.py`
- `tests/tools/test_process_registry.py`
- `tests/tools/test_process_registry_bg_shell_bash.py`

**1617:**

- `tests/tools/test_kill_verify_tree_death.py`
- `tests/tools/test_login_shell_path_preservation.py`

## Tests

**1616 — `tests/tools/test_process_registry_bg_shell_bash.py`:**

- `test_background_uses_bash_semantics_with_sh_login_shell`: real background probe for Bash identification, unquoted word splitting, and pipeline status.
- `test_background_uses_bash_semantics_with_zsh_login_shell`: the same probe with zsh; skipped if zsh is absent.
- `test_login_environment_still_loaded_and_bg_var_hidden`: profile exports survive Bash/sh login initialization and the command transport variable is hidden from the Bash-login command.
- `test_poll_and_kill_still_work`: registry running status, PID, and exit after termination.
- `test_foreground_unchanged`: the foreground `LocalEnvironment.execute` Bash-semantics probe.

**1616 — `tests/tools/test_process_registry.py`:** `TestSpawnRewriteCompoundBackground.test_pty_path_uses_rewritten_command` checks the PTY environment handoff with a mocked PTY spawn. Other changed assertions update shell mock targets, pipe command capture, and systemd/direct launch argv to the shared `bg_script` fixture.

**1617:**

- `tests/tools/test_login_shell_path_preservation.py::test_background_command_keeps_spawn_path_dirs_a_login_profile_resets`: updates the mock target for the existing background PATH-preservation regression.
- `tests/tools/test_kill_verify_tree_death.py::test_kill_reaps_children_spawned_during_the_grace_window`: explicitly installs `trap '' TERM` in the shell that spawns late children during termination.

Untested:

- End-to-end terminal-tool or `execute_code` dispatch; the added execution probes call the registry/environment directly.
- Real PTY execution of the Bash-semantics probe, Windows behavior, and non-sh-compatible login shells.
- Actual systemd service execution and durable-log exit recording with this new handoff; changed spawn assertions are not live service tests.

These were not rerun by the mirror pass.

## Applying

Apply 1616 before 1617, on the preceding fork patch series providing the process registry, local environment, and existing process-tree/PATH tests. These are incremental patches, not a standalone registry implementation. No new configuration option is introduced; the selected login shell still supplies initialization, while the foreground Bash resolver selects the command interpreter.

## Gotchas

- The command no longer runs inside the interactive login-shell wrapper itself. Signal-sensitive scripts and fixtures must request their intended signal behavior explicitly.
- Shell-specific functions, aliases, and syntax from a non-Bash login shell are not promised to work in the command's Bash process. Profile exports and shell language semantics are different concerns.
- zsh coverage is conditional on that executable being installed; listing the test does not prove it ran on every platform.

## Plugin?

No. This changes core subprocess argv/environment construction and its registry tests, rather than adding a plugin hook.
