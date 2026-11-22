"""Clock-independent display text. Tokens are internal; only typed parts go on the wire."""
import re

_TOKEN = re.compile(r'\x1e(ago|elapsed|remaining):([0-9.]+)\x1f')


def relative(at, style):
    return f'\x1e{style}:{float(at)}\x1f'


def clip(value, limit):
    """Clip literal tails, never discard a countdown to make room for its location.

    A temporal prefix may exceed the nominal phrase budget: retaining both the
    elapsed wait and its deadline is more important than clipping that prefix.
    """
    matches = list(_TOKEN.finditer(value))
    if not matches:
        return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'
    end = matches[-1].end()
    used = len(_TOKEN.sub('00h 00m', value[:end]))
    tail = value[end:]
    if used + len(tail) <= limit:
        return value
    keep = max(5 if tail.startswith(' left') else 0, limit - used - 1)
    return value[:end] + tail[:keep].rstrip() + '…'


def parts(value):
    matches = list(_TOKEN.finditer(value))
    if not matches:
        return None
    result, end = [], 0
    for m in matches:
        if m.start() > end:
            result.append(value[end:m.start()])
        result.append({'timestamp': float(m[2]), 'format': m[1]})
        end = m.end()
    if end < len(value):
        result.append(value[end:])
    return result


def encode(value):
    """Keep string fields backward-compatible; add clock_text templates beside them.

    No formatted ages, read clocks or internal tokens are serialized. Legacy clients
    see the static wording; new clients insert relative text using their local clock.
    """
    if isinstance(value, list):
        return [encode(v) for v in value]
    if not isinstance(value, dict):
        return value
    result, clocks = {}, {}
    for k, v in value.items():
        if isinstance(v, str) and (p := parts(v)):
            result[k] = _TOKEN.sub('', v).strip(' ·')
            clocks[k] = p
        elif isinstance(v, list) and any(isinstance(s, str) and parts(s) for s in v):
            result[k] = [_TOKEN.sub('', s).strip(' ·') if isinstance(s, str) else encode(s) for s in v]
            clocks[k] = [parts(s) or [s] if isinstance(s, str) else s for s in v]
        else:
            result[k] = encode(v)
    if clocks:
        result['clock_text'] = clocks
    return result
