#!/usr/bin/env python3
"""Sample the systemd-user gpg ssh agent; resume a stopped, verified agent.

Run as the agent's user. Every sample is durable JSONL; recoveries also go to
stderr (the systemd journal). No process-name-wide signals are ever sent.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def inspect_agent(pid):
    fd = os.pidfd_open(pid)
    try:
        proc = Path('/proc') / str(pid)
        argv0 = (proc / 'cmdline').read_bytes().split(b'\0')[0].decode()
        if (proc.stat().st_uid != os.getuid() or
                (proc / 'comm').read_text().strip() != 'gpg-agent' or
                Path(argv0).name != 'gpg-agent'):
            raise ValueError('refusing non-owned or non-gpg-agent target')
        state = next(line.split()[1] for line in (proc / 'status').read_text().splitlines() if line.startswith('State:'))
        recovered = state == 'T'
        if recovered:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        return {'pid': pid, 'state': state, 'recovered': recovered}
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log', type=Path, required=True)
    a = p.parse_args()
    event: dict = {'time': time.time()}
    try:
        result = subprocess.run(['systemctl', '--user', 'show', 'gpg-agent.service', '--property=MainPID,ActiveState'], capture_output=True, text=True, check=True, timeout=5)
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        pid = int(properties['MainPID'])
        if pid == 0 and properties.get('ActiveState') == 'inactive':
            event.update(pid=0, state='inactive', recovered=False)
        elif pid <= 0:
            raise ValueError('gpg-agent has no MainPID but is not inactive')
        else:
            event.update(inspect_agent(pid))
    except Exception as exc:
        event['error'] = str(exc)
    a.log.parent.mkdir(parents=True, exist_ok=True)
    if a.log.exists() and a.log.stat().st_size >= 8 * 1024 * 1024:
        a.log.replace(a.log.with_suffix(a.log.suffix + '.1'))
    with a.log.open('a') as out:
        out.write(json.dumps(event) + '\n')
    if event.get('error') or event.get('recovered'):
        print('ssh-agent-health INCIDENT ' + json.dumps(event), file=__import__('sys').stderr, flush=True)
    return 1 if event.get('error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
