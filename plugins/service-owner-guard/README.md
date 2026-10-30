# service-owner-guard

Refuses an agent's raw command that stops a service another card owns.

An owner is marked by `HERMES_OWNER_CARD` (launchd plist `EnvironmentVariables`, systemd `Environment=`, or a
process environment) or by an `OWNER.md` whose first line is `card: t_<hex>[@host]`, in the job's
`WorkingDirectory`, the script's directory, a `cd <dir>` in its shell command, or an ancestor of those below `$HOME`.
The parser is machine-investigate's `collect.py` (`valid_owner`, `read_owner_md`, `owner`); this plugin has none of its own,
and for ssh targets it sends that source to the remote host.

| Refused (owner found) | Never refused |
|---|---|
| `launchctl disable\|bootout\|unload\|remove\|stop` | `launchctl kickstart\|bootstrap\|enable\|load` |
| `systemctl [--user] stop\|disable\|mask\|kill` | `systemctl start\|restart\|reload` |
| `kill`, `pkill`, `killall` (local pids only) | a `bootout`/`unload` followed by `bootstrap`/`load` of the same label |

Also inside `ssh [opts] host '<cmd>'` (one `ssh -o BatchMode=yes -o ConnectTimeout=5` lookup, 8 s cap), `bash -c`,
execute_code Python and nested `tool_call` batches. Claude Code Bash gets `claude_hook.py` (`--install [settings.json]`).

Verdicts: owner found and no `owner-approved=<card id>` in the command: refused. The owner is the caller's own
`HERMES_KANBAN_TASK`: allowed. No owner: allowed, except `launchctl disable` and `systemctl disable|mask` (they persist
across reboots), which need `owner-checked=none`. Lookup failed (unreachable, timeout): the same two need
`owner-checked=none`; the others are allowed. Every refusal, override and lookup failure goes to
`~/.hermes/logs/service-owner-guard.log` (`SERVICE_OWNER_GUARD_LOG` overrides). `SERVICE_OWNER_GUARD=off` disables it.

Dry run: `python3 -c "import importlib.util as u;s=u.spec_from_file_location('g','plugins/service-owner-guard/__init__.py');g=u.module_from_spec(s);s.loader.exec_module(g);print(g.check('launchctl bootout gui/501/<label>'))"`

## Tests

`PYTHONPATH=$HOME/.hermes/hermes-agent ~/.hermes/hermes-agent/venv/bin/python -m pytest -q -p no:cacheprovider plugins/service-owner-guard/tests`
(Hermes hook, claude_hook stdin/stdout, ssh-wrapped with stubbed and real-unreachable lookup, shipped remote script,
reload/kickstart, local kill incl. ancestor process cwd, systemd, execute_code, nested tool_call.)
