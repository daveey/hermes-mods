# kanban-worker-nice

Kanban workers raise their own process niceness to 10 at plugin registration.
Children inherit it. Already-lower-priority workers are never raised in priority.
`HERMES_KANBAN_WORKER_NICE` sets the target (0 disables; Linux maximum is 19).

## Chat and subagent terminals

On listed **Linux** orchestration hosts, `terminal` shell commands also start at
nice 10, including the nested `hermes_tools.terminal()` RPC from `execute_code`.
The `tool_request` middleware prepends an absolute niceness floor to the shell,
before approvals and guards. It does not change the shared gateway process or
wrap the command in another shell, preserving terminal cwd/export tracking.
Background shell commands use the same rewritten command. Kanban workers retain
their inherited niceness without another increment.

The default host prefix is `hermes-hub`. As with cpu-load-guard, host matching is
case-insensitive and accepts hostname prefixes (including a short name for an
FQDN). Replace the list in `$HERMES_HOME/kanban-worker-nice.yaml`:

```yaml
protected_hosts:
  - orchestrator
```

Or use `HERMES_TERMINAL_NICE_HOSTS=orchestrator,build-host` (an empty value disables
the terminal policy). Flat YAML lists work without PyYAML. The target uses
`HERMES_KANBAN_WORKER_NICE`, default 10, clamped to 19 for these Linux shells.

Opt out **per command** with a leading literal assignment:

```sh
HUB_NICE_OK=1 nice
# or
 env HUB_NICE_OK=1 nice
```

This preserves the shell's inherited priority; it cannot restore zero if its
parent was already niced. A zero-priority chat therefore prints `10` normally
and `0` with the opt-out. A quoted mention later in a command is not an opt-out.
The gateway environment may also set `HUB_NICE_OK=1` to disable the policy.

`run_in_user_terminal` and unlisted hosts are unchanged. The terminal tool's
fixed five-second `http_readback` direct-exec curl is unchanged: its exact argv
contract does not permit a shell prefix. This is a scheduling default, not a
security boundary or permission to run heavy builds on an orchestration host.
Prefer execution workers for builds and suites. Admission must exclude `/proc/stat`
positive-nice user time for this to avoid false CPU-busy holds; system/IRQ/steal
CPU still counts as busy.

## Tests

Run `scripts/test-all -k kanban-worker-nice`. `tests_nice.py` verifies inherited
worker floors. `tests_terminal.py` covers host configuration, opt-out, idempotence,
worker exclusion, and real core dispatch/local-shell execution for both direct
terminal and the generated execute_code terminal stub plus RPC dispatcher. Core
integration checks skip when Hermes core is unavailable (standalone CI).
Both dispatch paths also execute core's real survivable-process tee wrapper,
which forks a subshell. The prefix targets `BASHPID`, not `$$` (the outer shell).
A dedicated zero-priority regression verifies command=10 and outer shell=0;
it explicitly skips under an already-niced launcher. Run once at nice 0 for
that proof and again under `nice -n 15` for inherited-floor compatibility.
