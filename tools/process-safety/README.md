# Process safety helpers (Linux)

## Owned scheduling fault injection

```sh
python3 tools/process-safety/starve_owned.py --timeout 90 --stopped .3 --runnable .05 -- python3 -m unittest my_test
```

Launches the command in a fresh session. STOP/CONT/KILL use pidfds, not process
names or numeric PID snapshots. The session leader stays unreaped until cleanup,
preventing session-ID reuse. Normal exit, deadline and SIGTERM resume/kill session
members and reap the leader. EPERM on one handle does not skip later handles.

Never use `pkill -STOP`, `killall -STOP`, or host-wide `pgrep` selection for test
starvation: the matching name may be a user's SSH agent. Daemonized children that
leave this session are deliberately excluded. An uncatchable SIGKILL/OOM of the
injector can strand its *own* paused children; this is not a sandbox or a general
process-tree supervisor. Use disposable containers for hostile fault injection.

## SSH agent health

`agent_health.py` reads systemd's user `gpg-agent.service` MainPID and ActiveState.
For a same-uid process whose comm and argv identify gpg-agent, it sends CONT only
when state is `T`. It does not intervene in debugger state `t`. GPG is nondumpable,
so `/proc/PID/exe` is intentionally not used. Inactive socket-activated services
are not incidents. Errors and recovery attempts are journal-visible INCIDENT
lines; JSONL retains every sample, rotating at 8 MiB with one backup.

The included user service/timer runs every 15 seconds. It assumes the checkout
is `~/code/hermes-mods`, and logs under `~/.hermes/logs`. These units are opt-in,
not installed by the general plugin installer. Install on an always-on Linux
host as the user owning the agent:

```sh
mkdir -p ~/.config/systemd/user
cp tools/process-safety/ssh-agent-health.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ssh-agent-health.timer
systemctl --user start ssh-agent-health.service
systemctl --user show ssh-agent-health.service -p Result -p ExecMainStatus
```

This bounds recovery to the timer interval plus scheduling latency, not every
possible SSH failure. It logs locally; it does not independently file Kanban
cards. A missing/broken agent is reported, not restarted. A 24-hour incident-free
window needs continuous signal tracing as well as samples: samples alone can
miss brief stops between timer ticks.

## Tests

```sh
/usr/bin/python3 -m unittest discover -s tools/process-safety -p 'test_*.py' -v
```

Ten tests cover: a real foreign same-name Python sentinel excluded from signal
handles; deadline and remaining-session-child cleanup; CLI SIGTERM cleanup; EPERM continuation; actual stopped
private GPG agent recovery; rejection of a different executable; systemctl
inactive/failed/malformed responses and JSONL; recovery records; bounded rotation.
No test sends STOP to a live shared agent. Host-wide old-harness reproduction is
intentionally not executed. Daemon escape, debugger-state exclusion and UID
mismatch are not independently exercised by this suite.
