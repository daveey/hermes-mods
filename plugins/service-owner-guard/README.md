# service-owner-guard

Refuses an agent's raw command that stops a service another card owns.

An owner is marked by `HERMES_OWNER_CARD` (launchd plist `EnvironmentVariables`, systemd `Environment=`, or a
process environment) or by an `OWNER.md` whose first line is `card: t_<hex>[@host]`, in the job's
`WorkingDirectory`, the script's directory, a `cd <dir>` in its shell command, or an ancestor of those below `$HOME`.
The stdlib parser is vendored in this plugin's `owner_lib.py` (`valid_owner`, `read_owner_md`, `owner`),
with its source revision recorded there. No skill or home-directory checkout is imported at runtime.
For ssh targets the guard sends these plugin-owned functions to the remote host.

| Refused (owner found) | Never refused |
|---|---|
| `launchctl disable\|bootout\|unload\|remove\|stop` | `launchctl kickstart\|bootstrap\|enable\|load` |
| `systemctl [--user] stop\|disable\|mask\|kill` | `systemctl start\|restart\|reload` |
| `kill`, `pkill`, `killall` (local or ssh target pids) | a `bootout`/`unload` followed by `bootstrap`/`load` of the same label |

Also inside `ssh [opts] host '<cmd>'` (one `ssh -o BatchMode=yes -o ConnectTimeout=5` lookup, 8 s cap), `bash -c`,
execute_code Python and nested `tool_call` batches. Claude Code Bash gets `claude_hook.py` (`--install [settings.json]`).
Remote kill lists are resolved together in one lookup. Remote `pkill` uses `pgrep` with the matching flags
(signal selectors removed); `killall` uses `pgrep -x <name>`. `kill -0` and signal-listing probes are skipped.
The remote script carries owner_lib.py's process-table, ancestor-chain, environment and owner functions, and runs
with stdlib Python 3.9+ on macOS or Linux `/proc`. PID ownership checks the process environment and each
ancestor process's exact cwd (not parent directories or arbitrary script paths).

This is a best-effort owner guard, not a shell sandbox: dynamic PID expressions and indirect command execution
are not resolved. `killall` matching deliberately uses pgrep's exact-name semantics rather than emulating every
platform-specific killall flag. SSH lookup argv is rebuilt from a narrow allowlist: `-i`, `-l`, `-p`
(including attached and clustered forms), and `-o IdentityFile=`, `User=`, `Port=`, `BatchMode=yes`,
`IdentitiesOnly=yes`, `ConnectTimeout=1..5`. Ports must be 1..65535. Harmless session/verbosity flags
are discarded; `-4`/`-6` are retained. Destinations must be literal host/user names, not options or shell text.
Unsupported options (including ProxyCommand, LocalCommand, KnownHostsCommand, `-F`, `-J`, `-E`, `-S`)
and malformed values refuse the stop before SSH is invoked, even for non-persistent stops or approval tokens.
This transport check also applies to reload-paired bootout/unload actions: the "never refused" reload
exception above applies only to supported transports.
Use a trusted preconfigured host alias for proxy routing; the user's existing SSH configuration is still trusted
and may run its own configured commands. A supported lookup failure is fail-open for non-persistent stops, and is logged.

Verdicts: owner found and no `owner-approved=<card id>` in the command: refused. The owner is the caller's own
`HERMES_KANBAN_TASK`: allowed. No owner: allowed, except `launchctl disable` and `systemctl disable|mask` (they persist
across reboots), which need `owner-checked=none`. Lookup failed (unreachable, timeout): the same two need
`owner-checked=none`; the others are allowed. Every refusal, override and lookup failure goes to
`~/.hermes/logs/service-owner-guard.log` (`SERVICE_OWNER_GUARD_LOG` overrides). `SERVICE_OWNER_GUARD=off` disables it.

Pinned owners: a local systemd unit with a `*pinned-owner*.conf` drop-in (for example the Ask Eve gateways'
`50-askeve-pinned-owner.conf`) is refused on `systemctl [--user] stop|disable|mask|kill`, `hermes [-p NAME] gateway stop`
(or `HERMES_HOME=…/profiles/NAME hermes gateway stop`), and `kill|pkill|killall` of the unit's top process (read from
`/proc/<pid>/cgroup`; children the gateway's own agent started stay killable), through `sudo -u`/`nice -n`/`timeout -s` wrappers,
with no owner card needed and no `owner-approved=` override (stopping one coupled owner breaks the operator checks, and
`hermes update` leaves a pinned gateway alone). The same applies to the commands inside a script run as `bash|sh|python
<file>` (file under 64 KB, literal commands only). Start/restart/status are never refused. Not covered: stops built from
variables in a script or a variable set in an earlier segment (`S=/x.sh; bash $S`), `xargs`/`cat f | bash`, pinned units on an
ssh host, `systemd-run` transient units, and anything run from an owner terminal (the intended path).

Dry run: `python3 -c "import importlib.util as u;s=u.spec_from_file_location('g','plugins/service-owner-guard/__init__.py');g=u.module_from_spec(s);s.loader.exec_module(g);print(g.check('launchctl bootout gui/501/<label>'))"`

## Tests

`/usr/bin/python3 -m unittest discover -s plugins/service-owner-guard/tests -v`

Or use `HERMES_MODS_PYTHON=<Hermes runtime python> scripts/test-all -k service-owner-guard --hermes-core <core checkout>`.
The Linux `/proc` fixture is skipped on macOS; run it on Linux with
`python3 -m unittest discover -s plugins/service-owner-guard/tests -p test_remote_kill.py -k linux_proc -v`.
(Hermes hook, claude_hook stdin/stdout, ssh-wrapped with stubbed and real-unreachable lookup, shipped remote script,
reload/kickstart, local kill incl. ancestor process cwd, systemd, execute_code, nested tool_call, remote kill/pkill/killall,
signal normalization, multi-PID lookup, real unreachable SSH with a 1 s cap, Python 3.9 compile, Linux env/cwd/ancestor fixtures.)

`tests/test_source_review.py` covers fake-SSH option/destination injection, unsupported/malformed transport refusal,
safe target-option canonicalization, terminal/Python/nested hook and data-heredoc entry paths, and sentinel
`collect.py` files at both former search roots. Its disposable plugin copy proves local ownership and remote
script generation work without any skill tree. Run with an explicit synthetic `HERMES_OWNER_CARD=t_feed123`
to exercise ancestor isolation too; use an isolated `HOME`, `HERMES_HOME`, and `TMPDIR` for all fixture writes.
