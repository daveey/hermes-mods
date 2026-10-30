# Backend restart guard

The Hermes plugin and Claude Code Bash adapter share the classifier in `__init__.py`.
They refuse recognized unwrapped restarts of shared Hermes backend services.

They also refuse recognized nonzero raw signals to live gateways, launcher parents and
Desktop serve/dashboard processes. Discovery reads numeric or JSON `gateway.pid` under
`HERMES_HOME` and sibling profiles, plus a fresh process snapshot at check time. SSH
commands resolve on the target with the same discovery source and bounded inspection;
only identity/user/port/hostname options are forwarded (never payload ProxyCommand,
LocalCommand, log files or port forwards). Unreadable state and failed discovery log
and fail open; the hooks never execute the supplied signal command.

`kill` (default, numeric and named selectors), `/bin/kill`, `sudo kill`, matching
`pkill`/`killall`, inline `python[3] -c` calls to literal `os.kill`/`os.killpg`,
and execute_code `os.kill`/`os.killpg` are checked. Signal 0 remains allowed.
Raw signals stay refused even inside the pause wrapper. For stack diagnostics use
`sample <pid> 5` on macOS or `py-spy dump --nonblocking --pid <pid>` on Linux,
never USR2/QUIT/ABRT or another signal. For a gateway restart use:

    python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- hermes gateway restart

For a systemd backend restart use:

    python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- systemctl --user restart hermes-always-on-dashboard.service

The wrapper is an operational pause/drain aid, not a guarantee of zero interruption:
it may run the command after a timeout or if admission cannot be held.

## Claude Code installation

The adapter is enabled through Claude's existing `~/.claude/settings.json` PreToolUse
configuration, alongside the other Bash guards, not through Hermes plugin enablement.
After linking this plugin into the Hermes home, run:

    python3 ~/.hermes/plugins/backend-restart-guard/claude_hook.py --install

An optional settings path supports isolated installation tests. Installation merges a
single Bash hook, preserves unrelated settings, and recognizes both source and symlink
paths to avoid duplicate registrations. The checked-in adapter owns registration;
there is no separate copied hook under `~/.claude/hooks`. Its code updates with the
plugin symlink. Claude Code owns when a running session reloads changed hook settings;
synthetic entrypoint verification does not prove an already-running Claude process
has reloaded them. No backend restart is needed to install the hook.

Normal invocation consumes Claude hook JSON from stdin, calls the shared classifier,
and exits 2 with a reason on stderr to block. It never executes the supplied command.
Only Bash PreToolUse payloads are checked. `HERMES_BACKEND_RESTART_GUARD=off` disables
both entrypoints.

## Tested coverage

    python3 -m unittest discover -s plugins/backend-restart-guard/tests -v

`tests/test_guard.py` exercises Hermes terminal, process_manage, execute_code, and nested
tool_call classification, bare/wrapped commands, unrelated commands and the off switch.
`tests/test_claude_hook.py` invokes the real Claude hook as a subprocess with JSON only:

- Direct local systemctl restart, newline-separated commands, bash -c and bash -lc.
- SSH remote compound git/heredoc/restart payloads; SSH -i/-o/-p options.
- Wrapped local, shell and SSH equivalents are allowed.
- A wrapper named only as a shell argument does not exempt a bare restart; a bare
  restart after a wrapped command remains blocked.
- Unrelated git, SSH and read-only systemctl calls remain allowed.
- Non-Bash and post-tool payloads are ignored.
- Registration preserves existing settings and settings symlinks, tolerates null hook
  maps, recognizes a hook path alias, and is idempotent. The off switch is tested.

`tests/test_signals.py` adds one named case per signal/control per entry path
(Hermes pre_tool_call and subprocess Claude stdin → deny JSON). Fixtures use an
isolated HERMES_HOME and sleeping children, never signal a real gateway, and only
terminate their own fixture children during cleanup. It also tests JSON/profile PID
files, backend-role/parent discovery, discovery failure logging, SSH inspection option
filtering, target-host discovery through a fake transport, and execute_code os.kill.

No test executes a restart, SSH signal payload or pause request. Host-specific forensic
transcripts belong outside this public repo; replay them as JSON to the configured
hook command, never as shell input.

## Boundaries

This is a best-effort lexical guard, not a shell security sandbox. It recognizes
literal commands, selected executable wrappers and at most three nested shell/SSH
layers. It does not resolve aliases, variables, generated commands, arbitrary scripts,
arbitrary Python scripts (other than the recognized inline -c os.kill calls), command
substitutions, variable-derived PIDs, Python aliases/dynamic arguments, or selector-only
pkill/killall invocations. Complex
shell grammar (including heredoc stdin) is not fully parsed; combined SSH option bundles
and options with values for sudo/systemctl are not fully modeled. The existing
Hermes execute_code checker is heuristic (including a broad pause-turns allowance).
The installer defaults to `~/.claude/settings.json`; custom Claude config directories
require an explicit settings path. Registration updates are not concurrent-writer locked,
and duplicate detection targets the exact Bash matcher. Classifier/load errors are
non-blocking hook errors, not a fail-closed security boundary.
Other clients, disabled hooks, modified hook settings and external operators remain
outside this Claude Bash entrypoint. No new coverage of those paths is claimed.

## Failure and prevention chain

Happened: a deployment command explicitly appended an unwrapped shared-backend
restart after a remote git merge. A supervisor restart stops live backend writers;
without the pause wrapper, no turn-boundary handoff is requested first.

Not caught: the previous mitigation registered only Hermes `pre_tool_call` (see
`register()`), while Claude Code sends Bash through its independent PreToolUse hook
list. A Hermes plugin cannot intercept that client. The original tests called only
the Hermes entrypoint, so they did not establish cross-client enforcement. The fix
adapts the shared classifier at Claude's actual command-hook boundary and tests that
boundary, plus idempotent activation. The historical attribution is retained privately
with the operational task, rather than publishing personal transcript data here.

Raw-signal prevention chain:
- Why could an agent signal the gateway? The original `_check_argv` checked service
  restarts and named serve/dashboard patterns, not numeric PID targets.
- Why did the owner guard not cover it? That separate guard requires an ownership
  marker; backend safety cannot depend on every runtime carrying an OWNER.md.
- Why was the gap not caught? Earlier tests covered restart commands but not a live
  pidfile fixture through both client entrypoints. The new signal matrix reproduced
  the missing denials before the classifier change and passes after it.
- Countermeasure: check actual runtime identities at the shared classifier boundary,
  preserve zero-signal probes, and name non-signalling diagnostics in the refusal
  and machine-investigate runbook. This is not a replacement for OS isolation.
