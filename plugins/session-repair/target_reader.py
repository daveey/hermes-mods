"""Opt-in, read-only source adapters for bounded owner reassessment.

The administrator configures fixed argv in target-readers.json, keyed by authority.
An awaiting ref selects only an authority/key, never shell text, a path or a command.
Use existing authenticated transport (for example a fixed SSH destination/command)
for remote sources. No source is installed or enabled by importing this module.
"""
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

SELECTOR = re.compile(r'owner-target:([A-Za-z0-9_.-]{1,64})/([A-Za-z0-9_.-]{1,64})\Z')
MAX_BYTES = 16384
TIMEOUT = 10


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite JSON constant')


def _json(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('oversized target response')
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)


def _run(argv):
    # stdout is bounded *during* acquisition; stderr is neither retained nor parsed.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    assert proc.stdout is not None
    data = bytearray()
    deadline = time.monotonic() + TIMEOUT
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not poll.select(remaining):
                    raise ValueError('target read timeout')
                block = os.read(proc.stdout.fileno(), MAX_BYTES + 1 - len(data))
                if not block:
                    break
                data.extend(block)
                if len(data) > MAX_BYTES:
                    raise ValueError('oversized target response')
        if proc.wait(timeout=max(.001, deadline-time.monotonic())):
            raise ValueError('target read failed')
        return bytes(data)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def valid_observation(value, ref, now):
    if (not isinstance(value, dict) or set(value) != {'version','observed_at','selector','target','disposition'}
            or type(value['version']) is not int or value['version'] != 1 or value['selector'] != ref
            or type(value['observed_at']) not in (int,float) or not math.isfinite(value['observed_at'])
            # The scan clock is frozen at tick start; a bounded source acquisition
            # can finish later. This tolerance never changes the certificate deadline.
            or not -30 <= now-value['observed_at'] <= 1200 or value['disposition'] != 'pending'):
        return False
    target = value['target']
    return (isinstance(target, dict) and set(target) == {'kind','identity','revision'}
            and target['kind'] in ('queue','update') and isinstance(target['identity'], str)
            and re.fullmatch(re.escape(ref) + r'@[0-9a-f]{64}', target['identity']) is not None
            and isinstance(target['revision'], str) and bool(re.fullmatch(
                r'(?:[A-Za-z0-9_.-]{1,64}:)?[0-9a-f]{40}', target['revision']))
            and ((':' in target['revision']) == (target['kind'] == 'queue')))


def read_target(ref, config_path, now):
    """Only independently acquired pending targets can defer an age-only finding."""
    unavailable = {'state':'target_unavailable','happened':False}
    match = SELECTOR.fullmatch(ref) if isinstance(ref, str) else None
    if not match:
        return unavailable
    try:
        fd = os.open(config_path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return unavailable
            config = _json(stream.read(MAX_BYTES + 1))
        if (not isinstance(config, dict) or set(config) != {'version','authorities'}
                or type(config['version']) is not int or config['version'] != 1
                or not isinstance(config['authorities'], dict)):
            return unavailable
        argv = config['authorities'].get(match[1])
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 32
                or not all(isinstance(a,str) and a and len(a) <= 4096 and '\x00' not in a for a in argv)
                or not Path(argv[0]).is_absolute()):
            return unavailable
        observation = _json(_run([*argv, 'observe', '--selector', ref]))
        if not valid_observation(observation, ref, now):
            return unavailable
        return {'state':'owner_target_pending','happened':False,'target_observation':observation}
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError):
        return unavailable
