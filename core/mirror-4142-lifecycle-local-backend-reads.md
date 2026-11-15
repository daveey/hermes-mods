# Lifecycle guard: no backend reads for missing paths on a local terminal

Mirror snapshot 4142; ordinals 4122, 4123.

## Behavior

When the terminal backend is `local`, the gateway lifecycle guard no longer asks the backend to read script paths that are missing on disk. A local backend shares the gateway's disk, so a path the scan cannot find there is final. Before this change each such path cost a backend read and counted against the 64-read remote cap, so a large tool script naming about 100 non-file path-like words was refused with "the scan budget was exhausted (remote reads at depth 2)".

For `docker`, `ssh` and other non-local backends the backend reader and the remote-read cap are kept. Lifecycle detection (for example `hermes gateway restart` inside a referenced script) and the scan budgets are unchanged.

Entry path: the `terminal` tool, through `gateway_lifecycle_block` (tested directly in 4122, and through `terminal_tool` in 4123 for the ssh case).

## How it works

- 4122: in `gateway_lifecycle_block` (`tools/terminal_tool_guards.py`), `read_remote_script` is now `None` when `env_type == "local"`; otherwise it is the existing lambda over `_read_script_for_guard(env, guard_cwd, p, _MAX_REFERENCED_SCRIPT_BYTES)`. The value is passed to `scan_gateway_lifecycle` unchanged otherwise.
- 4123: the remote-script fixture in the gateway restart loop tests now patches `_get_env_config` to report `env_type` `ssh`, so the fixture still exercises the remote-read path.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tools/terminal_tool_guards.py`
- `tests/tools/test_lifecycle_guard_local_backend_reads.py`
- `tests/hermes_cli/test_gateway_restart_loop.py`

Mail entries in the ordered series:

- 4122: [`084a98c217f207ad35860348231672771daa2048`](patches/4122-daveey-Lifecycle-guard-no-backend-reads-for-missing-.patch)
- 4123: [`0a2856d5e2e312c8f0b6c6926c1d4c3f3239900e`](patches/4123-test-terminal-select-ssh-for-remote-lifecycle-fixtur.patch)

## Tests

`tests/tools/test_lifecycle_guard_local_backend_reads.py` (new, 4122), using a recording backend whose `execute` finds nothing:

- `test_local_backend_never_reads_missing_paths_through_the_backend`: a script naming 100 missing paths, run with `env_type="local"`, returns no block and makes no `env.execute` call.
- `test_remote_backend_keeps_the_remote_read_cap` (parametrized `docker`, `ssh`): the same script is blocked with an error mentioning "remote reads", and the backend was consulted.
- `test_local_script_that_restarts_the_gateway_is_still_blocked`: a local script with 100 missing paths plus `hermes gateway restart` is still blocked with an error naming that command, with no backend calls.

`tests/hermes_cli/test_gateway_restart_loop.py` (changed, 4123), in `TestTerminalToolGatewayLifecycleGuardRemote`: the remote-script test patches `tt._get_env_config` to return `env_type` `ssh` so the remote script is read through the backend. The test file for this change is `tests/hermes_cli/test_gateway_restart_loop.py`, not `tests/tools/`.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The check is on the `env_type` string `local` only; other backend types keep the 64-read cap and can still fail closed with "remote reads" on scripts that name many missing paths.
- A path missing on the gateway disk is treated as final for local backends; the guard does not look at it any further.
- Detection rules and budgets other than the remote-read path are unchanged.
- Related lifecycle-guard docs: [budget refusal messages](mirror-3423-f-lifecycle-guard-budget-refusal-messages.md) and [heredoc masking](mirror-3423-h-lifecycle-guard-heredoc-masking.md).
- This document makes no deployment claim.

## Plugin?

No. The change is inside the core terminal tool's guard that decides whether a command may run; a plugin cannot replace that boundary.
