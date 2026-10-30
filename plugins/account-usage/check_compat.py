#!/usr/bin/env python3
"""Report drift between account-usage's audited pins and a Hermes core checkout.

Stdlib only; reads files, imports nothing from core. Exit 0 = every pin matches,
1 = drift (manual account choice is refused on hosts running that core until the
seams are re-audited and the pins refreshed), 2 = cannot check.

    check_compat.py [--core ~/.hermes/hermes-agent] [--json]

Pins checked:
- execution-compatibility.json: sha256 of each core file whose seams install() wraps
  or depends on: one digest or a list of every audited version (a core matching none
  makes install() refuse and selection fail closed);
- fast-rate-limit-fallback/account-usage-compatibility.json: sha256 of this plugin's
  selection.py (a mismatch makes fast-rate-limit-fallback refuse to compose with it).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing'


def _accepted(value):
    """A pin is one audited sha256 or a non-empty list of them (every audited core
    version, so mixed-core rollouts pass). Malformed = nothing accepted (drift)."""
    digests = [value] if isinstance(value, str) else value
    if not isinstance(digests, list) or not digests or not all(isinstance(d, str) for d in digests):
        return frozenset()
    return frozenset(digests)


def check(core, plugin=PLUGIN):
    core = Path(core).expanduser().resolve()
    pins = json.loads((plugin / 'execution-compatibility.json').read_text())
    drift = [{'pin': 'execution-compatibility.json', 'file': rel, 'expected': value, 'actual': _sha(core / rel)}
             for rel, value in pins.items() if _sha(core / rel) not in _accepted(value)]
    composition = plugin.parent / 'fast-rate-limit-fallback' / 'account-usage-compatibility.json'
    if composition.is_file():
        expected = json.loads(composition.read_text()).get('selection_sha256')
        actual = _sha(plugin / 'selection.py')
        if expected != actual:
            drift.append({'pin': 'fast-rate-limit-fallback/account-usage-compatibility.json',
                          'file': 'plugins/account-usage/selection.py', 'expected': expected, 'actual': actual})
    try:
        head = subprocess.run(['git', '-C', str(core), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        head = None
    return {'ok': not drift, 'core': str(core), 'core_head': head, 'drift': drift}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--core', default=str(Path.home() / '.hermes' / 'hermes-agent'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if not (Path(args.core).expanduser() / 'agent').is_dir():
        print(f'cannot check: {args.core} is not a Hermes core checkout', file=sys.stderr)
        return 2
    report = check(args.core)
    if args.json:
        print(json.dumps(report, indent=2))
    elif report['ok']:
        print(f"account-usage pins match core {report['core_head'] or report['core']}")
    else:
        print(f"account-usage pins DRIFT against core {report['core_head'] or report['core']}:")
        for item in report['drift']:
            expected = item['expected']
            shown = (expected[:12] if isinstance(expected, str) else
                     ' or '.join(str(e)[:12] for e in expected) if isinstance(expected, list) and expected
                     else f'malformed pin {expected!r}')
            print(f"  {item['pin']}: {item['file']} expected {shown} got {item['actual'][:12]}")
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
