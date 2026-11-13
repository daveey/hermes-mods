#!/usr/bin/env python3
"""py-spy front end that never stops an attached live process.

Install it as ``~/.local/bin/py-spy`` (a symlink to this file); the real binary
stays where ``uv tool install py-spy`` put it. Every ``record``/``top``/``dump``
that attaches to a PID (``-p``/``--pid``) without ``--nonblocking`` gets
``--nonblocking`` added. Blocking sampling ptrace-stops every thread of the
target on each sample; on a loaded host the sampler falls behind and keeps the
target stopped far past ``--duration`` (a 90 s recording held a hub backend
for 17 min, every Desktop request on that link timed out). The coach
profiler-safety hook only sees commands typed into a tool call, not a script
that runs py-spy, so the guard lives here, at the binary every path calls.

Not attached (``py-spy record -- python fixture.py``) is left alone: a fixture
you launch can be sampled blocking. ``PY_SPY_REAL`` overrides where the real
binary is.
"""
import os
import sys
from pathlib import Path

ATTACHING = {'record', 'top', 'dump'}
NOTE = ('py-spy guard: added --nonblocking (blocking sampling suspends the live target and '
        'can freeze it for minutes past --duration; see hermes-mods tools/process-safety)\n')


def rewrite(argv):
    """Return (argv with --nonblocking ensured for attached sampling, changed)."""
    args = list(argv)
    if not args or args[0] not in ATTACHING:
        return args, False
    head = args[:args.index('--')] if '--' in args else args
    if '--nonblocking' in head or '-h' in head or '--help' in head:
        return args, False
    attached = any(a in ('-p', '--pid') or a.startswith('--pid=') or (a.startswith('-p') and a[2:].isdigit())
                   for a in head[1:])
    if not attached:
        return args, False
    return [args[0], '--nonblocking'] + args[1:], True


def real_binary(argv0, env=os.environ):
    override = env.get('PY_SPY_REAL')
    if override:
        return override
    # ~/.local/bin/py-spy -> ~/.local/share/uv/tools/py-spy/bin/py-spy. Derived from the invoked
    # path, not $HOME: under sudo HOME may be /root while the tool lives in the caller's home.
    local = Path(os.path.abspath(argv0)).parent.parent
    candidate = local / 'share' / 'uv' / 'tools' / 'py-spy' / 'bin' / 'py-spy'
    if candidate.is_file() and os.path.realpath(candidate) != os.path.realpath(__file__):
        return str(candidate)
    return None


def main(argv=None):
    argv = sys.argv if argv is None else argv
    real = real_binary(argv[0])
    if real is None:
        sys.stderr.write('py-spy guard: real py-spy not found (set PY_SPY_REAL or `uv tool install py-spy`)\n')
        return 127
    args, changed = rewrite(argv[1:])
    if changed:
        sys.stderr.write(NOTE)
    os.execv(real, [real] + args)


if __name__ == '__main__':
    sys.exit(main())
