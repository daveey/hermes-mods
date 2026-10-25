"""Explicit user-service installer; importing or rendering never installs.

Use an absolute Python interpreter with PyYAML + jsonschema[format] installed.
Only fixed user-unit names are written. No root/system service or gateway restart.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'
UNIT = 'hermes-runners-collector.service'
LABEL = 'org.hermes.runners.collector'


def _argv(argv):
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError('absolute_executable_required')
    if any(type(v) is not str or not v or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in argv):
        raise ValueError('invalid_service_argument')
    return argv


def render_systemd(argv):
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    return (TEMPLATES / 'collector.service').read_text().replace('@EXEC_START@', ' '.join(map(quote, _argv(argv))))


def render_launchd(argv):
    args = ''.join('<string>' + escape(v) + '</string>' for v in _argv(argv))
    return (TEMPLATES / 'collector.plist').read_text().replace('@PROGRAM_ARGUMENTS@', args).encode()


def _target(platform, home):
    if platform == 'linux':
        return Path(home) / '.config/systemd/user' / UNIT
    if platform == 'darwin':
        return Path(home) / 'Library/LaunchAgents' / (LABEL + '.plist')
    raise ValueError('unsupported_platform')


def _call(runner, argv):
    runner(argv, check=True, timeout=30, shell=False, stdin=subprocess.DEVNULL)


def install(platform, argv, *, home=None, runner=subprocess.run):
    path = _target(platform, Path.home() if home is None else home)
    content = render_systemd(argv).encode() if platform == 'linux' else render_launchd(argv)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('unsafe_service_path')
    fd, temporary = tempfile.mkstemp(prefix='.collector-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if path.read_bytes() != content:
        raise RuntimeError('service_write_failed')
    if platform == 'linux':
        _call(runner, ['/usr/bin/systemctl', '--user', 'daemon-reload'])
        _call(runner, ['/usr/bin/systemctl', '--user', 'enable', '--now', UNIT])
        _call(runner, ['/usr/bin/systemctl', '--user', 'restart', UNIT])
        _call(runner, ['/usr/bin/systemctl', '--user', 'is-active', '--quiet', UNIT])
    else:
        _call(runner, ['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)])
        _call(runner, ['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'])
    return path


def uninstall(platform, *, home=None, runner=subprocess.run):
    path = _target(platform, Path.home() if home is None else home)
    if path.is_symlink():
        raise ValueError('unsafe_service_path')
    if platform == 'linux':
        _call(runner, ['/usr/bin/systemctl', '--user', 'disable', '--now', UNIT])
    else:
        _call(runner, ['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'])
    path.unlink(missing_ok=True)
    if platform == 'linux':
        _call(runner, ['/usr/bin/systemctl', '--user', 'daemon-reload'])
    if path.exists():
        raise RuntimeError('service_remove_failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--uninstall', action='store_true')
    parser.add_argument('--python', default=sys.executable)
    for name in ('config', 'runtime', 'state-dir', 'owner-gateway-id', 'owner-profile'):
        parser.add_argument('--' + name)
    parser.add_argument('--owner-epoch', type=int)
    args = parser.parse_args(argv)
    if args.uninstall:
        uninstall(sys.platform)
        return 0
    for name in ('config', 'runtime', 'state_dir', 'owner_gateway_id', 'owner_profile', 'owner_epoch'):
        if getattr(args, name) is None:
            parser.error('--' + name.replace('_', '-') + ' required for installation')
    for name in ('python', 'config', 'runtime', 'state_dir'):
        if not Path(getattr(args, name)).is_absolute():
            parser.error(name + ' must be absolute')
    command = [args.python, str(Path(__file__).with_name('run_collector.py').resolve())]
    for name in ('config', 'runtime', 'state_dir', 'owner_gateway_id', 'owner_profile', 'owner_epoch'):
        command.extend(['--' + name.replace('_', '-'), str(getattr(args, name))])
    install(sys.platform, command)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
