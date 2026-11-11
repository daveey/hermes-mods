"""Run scope maintenance with installed dependencies and collector restoration.

Use from a systemd user oneshot with ExecStopPost restarting the collector as a
second fence for SIGKILL/timeouts. No runner services or registrations are touched.
"""
import argparse
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def supervised(run, unit, command=subprocess.run):
    if unit != 'hermes-runners-collector.service':
        raise ValueError('unsupported_collector_unit')
    prefix = ['systemctl', '--user']
    status = command(prefix + ['is-active', '--quiet', unit], timeout=30)
    if status.returncode != 0:
        raise RuntimeError('collector_not_active')
    # Once stop is attempted, restore even if systemctl times out or fails.
    try:
        command(prefix + ['stop', unit], check=True, timeout=120)
        return run()
    finally:
        command(prefix + ['start', unit], check=True, timeout=120)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--core-root', required=True)
    args, remaining = parser.parse_known_args(argv)
    from plugins.runners.scripts import runtime_collector
    # Installed PM dependency activation, not full CLI/bootstrap or an update.
    with redirect_stdout(io.StringIO()):
        result = runtime_collector.main(['--core-root', args.core_root, '--check-core'])
    if result:
        return result
    from plugins.runners.scripts import reconcile_scopes
    return supervised(lambda: reconcile_scopes.main(remaining), 'hermes-runners-collector.service')


if __name__ == '__main__':
    raise SystemExit(main())
